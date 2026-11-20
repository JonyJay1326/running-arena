// 弹弹高尔夫 Bot —— 物理参数由 12 发试射标定（见 NOTES.md 第 3 节）
// 模型（g=1 缩放单位，与真实重力等比缩放，预测不受影响）：
//   u0 = 0.35*power - 0.26；炮口 = 脚底 + 35*(−sin(ga), −cos(ga))（ga=groundAngle，右升坡为正）
//   发射方向 = (dir*cos(aEff), −sin(aEff))，aEff = angle + dir*groundAngle（angle 为相对水平角）
//   重力 g=1（屏幕 y 向下，故 +½t²）；风 ax = 0.0372*wind（恒定，实测不随高度变化）
import { createInterface } from 'node:readline';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const MEMDIR = join(__dirname, 'memory');

const U_A = 0.35, U_B = -0.26;      // u0 = U_A*power + U_B
const MUZZLE_H = 35;                 // 炮口高于脚底
const WIND_K = 0.0372;               // ax = WIND_K * wind
const DT_C = 0.01, DT_F = 0.0015;    // 粗/细模拟步长
const TMAX = 90;
const DEG = Math.PI / 180;
const KIND = {
  single: { dmg: 120, offs: [0] },
  burst:  { dmg: 195, offs: [0] },   // 3 发同轨×65，实测总伤 195（第七洞 a1）
  spread: { dmg: 55,  offs: [-6, 0, 6] },
  dig:    { dmg: 30,  offs: [0] },
};

// ---------- 状态 ----------
let cfg = null, GW = 400, GH = 225, CELL = 4, grid = null;
let hole = null, actor = null, objects = [], lastResult = null;
let strokes = 0, flyLeft = 1;
let calib = { disp: { 0: 550, 15: 520, 25: 500, 32: 400, 38: 280, 42: 85, 50: 82, 60: 50, 80: 30, 110: 12 }, directDisp: 200 }; // Bug 位移响应表（爆距→净位移）；<38 坑覆盖 Bug 为强弹飞
let noProgress = 0;
let lastBugMove = null;
// 已试动作表：key -> {prog}。无进展的已试动作不再重复（第一轮 7 洞死循环的根因：
// 搜索确定 + 状态不变 ⇒ 每轮选出同一候选）。key = 弹种|角度×2|力度|方向|站位桶。
const shotLog = new Map();
let lastKey = null;
const fbCount = { n: 0 };
function keyOf(a, kind) {
  return `${kind}|${Math.round(a.ang * 2)}|${Math.round(a.power)}|${a.dir}|${Math.round(a.st.x / 24)}`;
}

function loadCalib() { try { const s = JSON.parse(readFileSync(join(MEMDIR, 'calib.json'), 'utf8')); if (s.disp) calib.disp = { ...calib.disp, ...s.disp }; } catch {} }
function saveMemory() {
  try {
    mkdirSync(MEMDIR, { recursive: true });
    writeFileSync(join(MEMDIR, 'calib.json'), JSON.stringify(calib));
  } catch {}
}

// ---------- 地形 ----------
function parseTerrain(t) {
  GW = t.width; GH = t.height; CELL = t.cell;
  grid = new Uint8Array(GW * GH);
  let i = 0;
  for (const [v, n] of t.rle) { const c = v ? 1 : 0; for (let k = 0; k < n && i < grid.length; k++) grid[i++] = c; }
}
function solidPx(x, y) {
  const cx = Math.floor(x / CELL), cy = Math.floor(y / CELL);
  if (cx < 0 || cx >= GW || cy < 0) return false;
  if (cy >= GH) return true;
  return grid[cy * GW + cx] === 1;
}
function surfaceY(x) { // 该列最上方实心格的顶边
  const cx = Math.floor(x / CELL);
  if (cx < 0 || cx >= GW) return null;
  const base = cx;
  for (let cy = 0; cy < GH; cy++) if (grid[cy * GW + base]) return cy * CELL;
  return null;
}
function slopeAt(x) {
  const a = surfaceY(x - 8), b = surfaceY(x + 8);
  if (a == null || b == null) return null;
  return Math.atan2(a - b, 16) / DEG;
}
function standable(x, minFeet) {
  if (x < 6 || x > 1600 - 6) return null;
  const s = surfaceY(x); if (s == null) return null;
  const ga = slopeAt(x); if (ga == null || Math.abs(ga) > 42) return null;
  if (!solidPx(x, s + 2) || !solidPx(x, s + 6)) return null;
  const l = surfaceY(x - 14), r = surfaceY(x + 14);
  if (l == null || r == null || Math.abs(l - s) > 30 || Math.abs(r - s) > 30) return null;
  if (minFeet != null && s < minFeet + 2) return null;   // 脚底不低于 minFeet（留 2px 余量）
  for (const o of objects) {
    if (o.removed || o.broken) continue;
    if (Math.abs(o.x - x) < 22 && o.y > s - 46 && o.y < s + 30) return null;
  }
  return { x, footY: s, ga };
}
function muzzleOf(st) {
  const ga = st.ga * DEG;
  return { mx: st.x - MUZZLE_H * Math.sin(ga), my: st.footY - MUZZLE_H * Math.cos(ga) };
}

// ---------- 物体 ----------
function objBox(o) {
  if (o.type === 'bug') return { cx: o.x, cy: o.y - (o.radius || 14), r: o.radius || 14 };
  const w = o.width || 28, h = o.height || 28;
  return { x0: o.x - w / 2, y0: o.y - h, x1: o.x + w / 2, y1: o.y };
}
function objCenter(o) {
  if (o.type === 'bug') return { x: o.x, y: o.y - (o.radius || 14) };
  return { x: o.x, y: o.y - (o.height || 28) / 2 };
}
function pointInObj(x, y, o) {
  if (o.removed || o.broken) return false;
  const s = objBox(o);
  if (s.r != null) { const dx = x - s.cx, dy = y - s.cy; return dx * dx + dy * dy <= s.r * s.r; }
  return x >= s.x0 && x <= s.x1 && y >= s.y0 && y <= s.y1;
}
function falloff(ex, ey, o) { // 到物体最近点的距离衰减
  const s = objBox(o);
  let d;
  if (s.r != null) d = Math.max(0, Math.hypot(ex - s.cx, ey - s.cy) - s.r);
  else {
    const dx = Math.max(s.x0 - ex, 0, ex - s.x1);
    const dy = Math.max(s.y0 - ey, 0, ey - s.y1);
    d = Math.hypot(dx, dy);
  }
  return d < 30 ? 1 - d / 30 : 0;
}

// ---------- 弹道模拟 ----------
// angDeg = 绝对发射仰角（相对水平，已叠加斜坡）；方向 = (dir*cos(a), -sin(a))
function simulate(mx, my, angDeg, power, dir, wind, dt) {
  const u = U_A * power + U_B;
  if (u <= 0.5) return null;
  const th = angDeg * DEG;
  const vx = Math.cos(th) * dir * u, vy = -Math.sin(th) * u;
  const ax = WIND_K * wind;
  let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9, hasObj = false;
  for (const o of objects) {
    if (o.removed || o.broken) continue;
    hasObj = true;
    const s = objBox(o);
    const x0 = s.r != null ? s.cx - s.r : s.x0, x1 = s.r != null ? s.cx + s.r : s.x1;
    const y0 = s.r != null ? s.cy - s.r : s.y0, y1 = s.r != null ? s.cy + s.r : s.y1;
    if (x0 < minX) minX = x0; if (x1 > maxX) maxX = x1;
    if (y0 < minY) minY = y0; if (y1 > maxY) maxY = y1;
  }
  const n = Math.ceil(TMAX / dt);
  let lx = mx, ly = my;
  for (let i = 0; i < n; i++) {
    const t = (i + 1) * dt;
    const x = mx + vx * t + 0.5 * ax * t * t;
    const y = my + vy * t + 0.5 * t * t;
    lx = x; ly = y;
    if (x < -10 || x > 1610 || y > 905) return { x, y, t, lost: true };
    const cx = Math.floor(x / CELL), cy = Math.floor(y / CELL);
    if (cy >= GH) return { x, y, t, hit: 'ground' };
    if (cx >= 0 && cx < GW && cy >= 0 && grid[cy * GW + cx]) return { x, y, t, hit: 'ground' };
    if (hasObj && x >= minX - 2 && x <= maxX + 2 && y >= minY - 2 && y <= maxY + 2) {
      for (const o of objects) if (pointInObj(x, y, o)) return { x, y, t, hit: o };
    }
  }
  return { x: lx, y: ly, t: TMAX, lost: true };
}
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

// ---------- 目标 ----------
function tokenTargets() {
  const g = hole.goal;
  if (g !== 'tokens' && g !== 'both') return [];
  return objects.filter(o => !o.removed && !o.broken && (o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken'));
}
function bugTarget() {
  const g = hole.goal;
  if (g !== 'bug' && g !== 'both') return null;
  return objects.find(o => o.type === 'bug' && !o.removed && !o.broken && !o.inRecycle) || null;
}
function chickens() { return objects.filter(o => o.type === 'chicken' && !o.removed && !o.broken); }
function recycleCenter() {
  const r = hole.recycle;
  return r ? { x: (r.x0 + r.x1) / 2, y: (r.y0 + r.y1) / 2 } : { x: 800, y: 780 };
}

// ----------  aiming ----------
function standList(targetX) {
  const out = [];
  const t = hole.tee;
  if (t && hole.teeActive !== false) {
    const mf = t.minFeet;
    const step = Math.max(20, Math.floor((t.x1 - t.x0) / 8));
    for (let x = t.x0 + 2; x <= t.x1 - 2; x += step) { const s = standable(x, mf); if (s) out.push(s); }
  }
  const cur = { x: actor.x, footY: actor.y + (cfg?.BODY?.footOffset ?? 12), ga: actor.groundAngle || 0 };
  if (!out.some(s => Math.abs(s.x - cur.x) < 4)) out.push(cur);
  // 去重 + 限流；连续无进展时优先远端站位（换一条弹道）
  const uniq = [];
  for (const s of out) if (!uniq.some(u => Math.abs(u.x - s.x) < 6)) uniq.push(s);
  if (noProgress >= 2) {
    uniq.sort((a, b) => Math.abs(b.x - targetX) - Math.abs(a.x - targetX));
    return uniq.slice(0, 7);
  }
  uniq.sort((a, b) => Math.abs(a.x - targetX) - Math.abs(b.x - targetX));
  return uniq.slice(0, 5);
}

function aimThrough(st, tx, ty) {
  // 扫描飞行时间 t，求经过 (tx,ty) 的（相对）angle/power/dir
  const { mx, my } = muzzleOf(st);
  const wind = hole.wind || 0;
  const out = [];
  for (let t = 1.2; t <= TMAX; t += 0.7) {
    const A = (tx - mx - 0.5 * WIND_K * wind * t * t) / t;   // u*cos(aEff)*dir
    const C = (my - ty + 0.5 * t * t) / t;                   // u*sin(aEff)
    const u = Math.hypot(A, C);
    if (u < 2) continue;
    for (const dir of [1, -1]) {
      const aEff = Math.atan2(C, dir * A) / DEG;             // 绝对仰角
      const rel = aEff - dir * st.ga;                        // 协议 angle（相对身体）
      if (rel < 1 || rel > 85) continue;
      let p = (u - U_B) / U_A;
      if (p < 2 || p > 100) continue;   // 超射程不产生候选（截断会打偏）
      out.push({ ang: rel, power: p, dir, st, aEff });
    }
  }
  return out;
}

function scoreAim(a, kind, targets, bugT, chks, dt) {
  const offs = KIND[kind].offs;
  const imps = [];
  for (const off of offs) {
    const im = simulate(a.st.mx, a.st.my, a.aEff + off, a.power, a.dir, hole.wind || 0, dt);
    if (!im || im.lost) return -1e6;
    imps.push(im);
  }
  const main = imps[offs.length === 3 ? 1 : 0];
  let sc = 0;
  for (const tg of targets) {
    let dmg = 0;
    for (const im of imps) dmg += KIND[kind].dmg * falloff(im.x, im.y, tg);
    const dm = dist(main, objCenter(tg));
    const hp = tg.hp ?? 1;
    if (dmg >= hp) sc += 300 + Math.max(0, 80 - dm);
    else if (dmg > 0) sc += 120 * dmg / hp;
    else sc -= Math.min(500, dm) * 0.3;
  }
  for (const c of chks) {
    const cc = objCenter(c);
    for (const im of imps) if (dist(im, cc) < 46) sc -= 1200;
    for (const im of imps) if (im.hit && im.hit.type === 'chicken') sc -= 4000;
  }
  if (bugT) {
    const bc = objCenter(bugT);
    const rc = recycleCenter();
    const direct = imps.some(im => im.hit && im.hit === bugT);
    const d = Math.hypot(main.x - bc.x, main.y - bc.y);
    let disp = direct ? calib.directDisp : dispEst(d);
    // 实测：净位移基本沿水平方向（爆点→Bug 的水平方向；直推时为远离射手）
    let sx = bc.x - main.x;
    if (Math.abs(sx) < 4) sx = bc.x - a.st.x;
    const s = sx >= 0 ? 1 : -1;
    const land = { x: bc.x + s * disp, y: bc.y };
    const r = hole.recycle;
    const inZone = r && land.x >= r.x0 && land.x <= r.x1 && land.y >= r.y0 && land.y <= r.y1;
    if (inZone) {
      const zc = { x: (r.x0 + r.x1) / 2, y: (r.y0 + r.y1) / 2 };
      sc += 520 - Math.hypot(land.x - zc.x, land.y - zc.y) * 0.4; // 入区且偏好靠近区心
    } else {
      let dz;
      if (r) {
        const dx = Math.max(r.x0 - land.x, 0, land.x - r.x1);
        const dy = Math.max(r.y0 - land.y, 0, land.y - r.y1);
        dz = Math.hypot(dx, dy);
      } else dz = 1e6;
      sc += Math.max(0, 320 - dz * 0.7);
    }
    if (land.x < 24 || land.x > 1576) sc -= 450;   // 别推出地图
    for (const c of chks) if (Math.hypot(land.x - c.x, land.y - c.y) < 58) sc -= 260; // 别撞尖叫鸡
  }
  return sc;
}
function dispEst(d) {
  const ks = Object.keys(calib.disp).map(Number).sort((a, b) => a - b);
  if (d <= ks[0]) return calib.disp[ks[0]];
  if (d >= ks[ks.length - 1]) return calib.disp[ks[ks.length - 1]];
  for (let i = 1; i < ks.length; i++) {
    if (d <= ks[i]) {
      const a = ks[i - 1], b = ks[i];
      return calib.disp[a] + (calib.disp[b] - calib.disp[a]) * (d - a) / (b - a);
    }
  }
  return 0;
}
function aimDamages(a, kind, targets) {
  if (!targets.length) return false;
  const offs = KIND[kind].offs;
  for (const off of offs) {
    const im = simulate(a.st.mx, a.st.my, a.aEff + off, a.power, a.dir, hole.wind || 0, DT_C);
    if (!im || im.lost) continue;
    for (const tg of targets) if (KIND[kind].dmg * falloff(im.x, im.y, tg) > 0) return true;
  }
  return false;
}

function refine(best, kind, targets, bugT, chks) {
  const mk = (ang, power, dir, st) => ({ ang: clamp(ang, 0.5, 84.5), power, dir, st, aEff: clamp(ang, 0.5, 84.5) + dir * st.ga });
  let cur = mk(best.ang, best.power, best.dir, best.st);
  let sc = scoreAim(cur, kind, targets, bugT, chks, DT_C);
  for (let iter = 0; iter < 2; iter++) {
    let improved = false;
    for (const da of [-1.5, -0.5, 0.5, 1.5]) {
      for (const dp of [-4, -1.5, 1.5, 4]) {
        const c = mk(cur.ang + da, Math.max(2, Math.min(100, cur.power + dp)), cur.dir, cur.st);
        const s = scoreAim(c, kind, targets, bugT, chks, DT_C);
        if (s > sc + 1e-9) { sc = s; cur = c; improved = true; }
      }
    }
    if (!improved) break;
  }
  return { a: cur, sc };
}

function decide() {
  const targets = tokenTargets();
  const bugT = bugTarget();
  const chks = chickens();
  if (!targets.length && !bugT) return safeFallback();

  const t0 = Date.now();
  let best = null; // {a, sc, kind}

  // 主目标点
  const aimPts = [];
  if (targets.length) {
    targets.sort((a, b) => dist(objCenter(a), { x: actor.x, y: actor.y }) - dist(objCenter(b), { x: actor.x, y: actor.y }));
    aimPts.push(...targets.slice(0, 3).map(t => ({ p: objCenter(t), tag: 'tk' })));
  }
  const bugPts = [];
  if (bugT) {
    const bc = objCenter(bugT), rc = recycleCenter();
    const sx = rc.x !== bc.x ? Math.sign(rc.x - bc.x) : 1;   // 水平推向回收区
    // 不用「直推」候选：实测直推位移 45/248/304/641 四点方差极大（练习本轮又一次 641），
    // 方向虽为远离射手但距离不可预测；只使用已标定的侧向爆距档位。
    for (const d of [15, 25, 38, 50, 70, 95]) {
      bugPts.push({ p: { x: bc.x - sx * d, y: bugT.y }, tag: 'bug' + d });
    }
  }

  const stands = standList((aimPts[0] || bugPts[0]).p.x);
  const kinds = [];
  if (targets.length) {
    const tg = targets[0];
    const rem = Math.max(1, (hole.cap ?? 99) - strokes);
    const mxHp = Math.max(...targets.map(t => t.hp ?? 1));
    kinds.push('single');
    if (tg.type === 'bigToken' && (tg.hp ?? 240) > 130) kinds.push('burst');
    // 单发在剩余杆数内打不烂最高血目标时，换上多弹丸弹种（第一轮 05 洞：single×8=960<1000）
    if (KIND.single.dmg * rem < mxHp) kinds.push('burst', 'spread');
  } else kinds.push('single');
  const kindList = [...new Set(kinds)];

  const evalPts = (pts, allowTried) => {
    for (const ap of pts) {
      for (const st of stands) {
        const m = muzzleOf(st); st.mx = m.mx; st.my = m.my;
        const cands = aimThrough(st, ap.p.x, ap.p.y);
        for (const kind of kindList) {
          for (const a of cands) {
            if (!allowTried) {
              const seen = shotLog.get(keyOf(a, kind));
              if (seen && !seen.prog) continue; // 无进展的已试动作：跳过
            }
            const sc = scoreAim(a, kind, targets, bugT, chks, DT_C);
            if (!best || sc > best.sc) best = { a: { ...a }, sc, kind, ap: ap.tag };
            if (Date.now() - t0 > 300) break;
          }
          if (Date.now() - t0 > 300) break;
        }
        if (Date.now() - t0 > 300) break;
      }
      if (Date.now() - t0 > 300) break;
      if (best && best.sc > 320) break; // 已能击杀，不必再试其他目标点
    }
  };
  // 先打 token（确定进展），完全打不到 token 才处理 Bug
  evalPts(aimPts, false);
  if (!best || !aimDamages(best.a, best.kind, targets)) evalPts(bugPts, false);
  if (!best) { evalPts(aimPts, true); evalPts(bugPts, true); } // 全新候选耗尽：允许复用已试动作

  if (!best) return safeFallback();
  if (process.env.BOT_DEBUG3) {
    const offs = KIND[best.kind].offs;
    const imps = offs.map(off => simulate(best.a.st.mx, best.a.st.my, best.a.aEff + off, best.a.power, best.a.dir, hole.wind || 0, DT_C));
    const main = imps[offs.length === 3 ? 1 : 0];
    const parts = [];
    for (const tg of targets) {
      let dmg = 0; for (const im of imps) dmg += KIND[best.kind].dmg * falloff(im.x, im.y, tg);
      parts.push(`tk#${tg.id} dmg=${dmg.toFixed(0)}/${tg.hp ?? 1} dm=${dist(main, objCenter(tg)).toFixed(0)}`);
    }
    if (bugT) {
      const bc = objCenter(bugT), rc = recycleCenter();
      let px = rc.x - bc.x, py = rc.y - bc.y; const pl = Math.hypot(px, py) || 1; px /= pl; py /= pl;
      const direct = imps.some(im => im.hit && im.hit === bugT);
      const d = Math.hypot(main.x - bc.x, main.y - bc.y);
      const disp = direct ? calib.directDisp : dispEst(d);
      parts.push(`bug direct=${direct} d=${d.toFixed(0)} disp=${disp.toFixed(0)} land=(${(bc.x + px * disp).toFixed(0)},${(bc.y + py * disp).toFixed(0)})`);
    }
    process.stderr.write('SCORE ' + best.sc.toFixed(0) + ' | ' + parts.join(' | ') + '\n');
  }
  if (process.env.BOT_DEBUG) {
    const mm = muzzleOf(best.a.st);
    const im = simulate(mm.mx, mm.my, best.a.aEff, best.a.power, best.a.dir, hole.wind || 0, DT_F);
    process.stderr.write(`best ${best.kind} ang=${best.a.ang.toFixed(1)} pow=${best.a.power.toFixed(1)} dir=${best.a.dir} stand=${best.a.st.x.toFixed(0)} sc=${best.sc.toFixed(0)} tag=${best.ap} imp=(${im && im.x.toFixed(0)},${im && im.y.toFixed(0)})${im && im.hit && im.hit !== 'ground' ? ' hit#' + im.hit.id : ''} ideal=(${aimPts.find(a => a.tag === best.ap)?.p.x.toFixed(0)},${aimPts.find(a => a.tag === best.ap)?.p.y.toFixed(0)})\n`);
  }
  const rf = refine(best.a, best.kind, targets, bugT, chks);
  if (rf.sc > best.sc) { best.a = rf.a; best.sc = rf.sc; }

  // 低分且能飞：考虑纸飞机靠近目标（简化版）；卡住时放宽阈值
  if (best.sc < (noProgress >= 2 ? 400 : 120) && flyLeft > 0 && hole.teeActive !== false && Date.now() - t0 < 250) {
    const flyAim = tryFly((aimPts[0] || bugPts[0]).p, targets, bugT, chks, t0);
    if (flyAim) { shotLog.set('fly', { prog: false }); lastKey = 'fly'; return flyAim; }
  }

  let a = best.a;
  let key = keyOf(a, best.kind);
  const seen = shotLog.get(key);
  if (seen && !seen.prog) {
    // 全部可行候选都已试过且无进展：加确定性扰动逃逸（角度/力度轮换，非随机）
    fbCount.n++;
    const da = ((fbCount.n % 3) - 1) * 2.5, dp = ((fbCount.n % 2) * 2 - 1) * 3;
    a = { ...a, ang: clamp(a.ang + da, 0.5, 84.5), power: clamp(a.power + dp, 2, 100) };
    a.aEff = a.ang + a.dir * a.st.ga;
    key = keyOf(a, best.kind);
  }
  shotLog.set(key, { prog: false }); // 占位；本杆是否有进展由下一回合 analyzeLast 回填
  lastKey = key;

  const st = a.st;
  const act = { attack: { kind: best.kind, angle: clamp(a.ang, 0, 85), power: clamp(Math.round(a.power), 1, 100), dir: a.dir } };
  // 只在收益明显时移动（初始站位永远合法，减少非法动作暴露）
  if (hole.teeActive !== false && Math.abs(st.x - actor.x) > 24) act.moveX = clamp(Math.round(st.x), Math.ceil(hole.tee?.x0 ?? 0), Math.floor(hole.tee?.x1 ?? 1600));
  return act;
}

function tryFly(aimP, targets, bugT, chks, t0) {
  // 在目标附近找可站落点，再找飞到那里的飞机参数；评估落地后的一杆
  const tp = aimP;
  const cand = [];
  for (let dx = -320; dx <= 320; dx += 40) {
    const x = tp.x + dx;
    if (x < 10 || x > 1590) continue;
    const s = standable(x, hole.tee?.minFeet);
    if (s) cand.push(s);
  }
  if (!cand.length) return null;
  let best = null;
  for (const st of cand.slice(0, 8)) {
    const m = muzzleOf(st);
    for (let t = 1.5; t <= 60; t += 0.6) {
      // 飞机从当前位置炮口发出，落到 st 附近
      const mm = muzzleOf({ x: actor.x, footY: actor.y + 12, ga: actor.groundAngle || 0 });
      const A = (st.x - mm.mx - 0.5 * WIND_K * (hole.wind || 0) * t * t) / t;
      const C = (mm.my - st.footY + 0.5 * t * t) / t;
      const u = Math.hypot(A, C);
      if (u < 2) continue;
      for (const dir of [1, -1]) {
        const th = Math.atan2(C, dir * A) / DEG;
        if (th < 1 || th > 89) continue;
        let p = (u - U_B) / U_A;
        if (p < 2 || p > 100) continue;
        // 模拟飞机（不撞物体）
        const im = flySim(mm.mx, mm.my, th, p, dir);
        if (!im || im.lost) continue;
        const d = Math.hypot(im.x - st.x, im.y - st.footY);
        const land = standable(im.x, hole.tee?.minFeet);
        if (!land || d > 60) continue;
        // 落地后打一杆的得分
        const st2 = { ...land, mx: land.x, my: land.footY - MUZZLE_H };
        const a2 = aimThrough(st2, tp.x, tp.y);
        let bsc = -1e6, ba = null;
        for (const a of a2) {
          const sc = scoreAim(a, 'single', targets, bugT, chks, DT_C);
          if (sc > bsc) { bsc = sc; ba = a; }
        }
        if (ba && (!best || bsc > best.sc)) best = { sc: bsc, fly: { angle: clamp(th, 0, 90), power: clamp(Math.round(p), 1, 100), dir } };
      }
      if (Date.now() - t0 > 480) break;
    }
    if (Date.now() - t0 > 480) break;
  }
  if (best && best.sc > 300) return { fly: best.fly };
  return null;
}
function flySim(mx, my, angDeg, power, dir) {
  const u = U_A * power + U_B;
  const th = angDeg * DEG;
  const vx = Math.cos(th) * dir * u, vy = -Math.sin(th) * u;
  const ax = WIND_K * (hole.wind || 0);
  for (let t = 0.01; t < TMAX; t += 0.01) {
    const x = mx + vx * t + 0.5 * ax * t * t;
    const y = my + vy * t + 0.5 * t * t;
    if (x < 0 || x > 1600 || y > 900) return { lost: true };
    if (solidPx(x, y)) return { x, y };
  }
  return { lost: true };
}

function safeFallback() {
  fbCount.n++;
  const ang = 30 + (fbCount.n % 5) * 10; // 30/40/50/60/70 循环，避免同一兜底动作连打
  return { attack: { kind: 'single', angle: ang, power: 60, dir: 1 } };
}
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

// ---------- 赛后分析（校准 Bug 推力） ----------
function analyzeLast() {
  if (!lastResult) return;
  const lr = lastResult;
  const impacts = lr.impacts || [];
  for (const ch of lr.objectChanges || []) {
    const o = objects.find(x => x.id === ch.id);
    if (!o || !ch.before || !ch.after) continue;
    if (o.type === 'bug') {
      const b = ch.before, a = ch.after;
      const mv = Math.hypot(a.x - b.x, a.y - b.y);
      const rc = recycleCenter();
      let px = rc.x - b.x, py = rc.y - b.y; const pl = Math.hypot(px, py) || 1; px /= pl; py /= pl;
      const align = ((a.x - b.x) * px + (a.y - b.y) * py) / (mv || 1);
      const imp = impacts.find(i => i.x != null);
      const blastD = imp ? Math.hypot(imp.x - b.x, imp.y - (b.y - 14)) : null;
      lastBugMove = { mv, align, blastD, from: b, to: a };
      // 更新位移响应表：桶 = 爆距/15 取整
      const direct = (lr.impacts || []).some(i => i.objectId === ch.id);
      if (blastD != null && mv > 1 && align > 0.2) {
        if (direct) calib.directDisp = calib.directDisp * 0.4 + mv * 0.6;
        else {
          const bk = String(Math.round(blastD / 15) * 15);
          const prev = calib.disp[bk];
          calib.disp[bk] = prev == null ? mv : prev * 0.4 + mv * 0.6;
        }
      }
    }
  }
  // 无进展计数
  let prog = false;
  for (const ch of lr.objectChanges || []) if (ch.damage > 0 || (ch.before && ch.after && (ch.before.x !== ch.after.x || ch.before.y !== ch.after.y))) prog = true;
  noProgress = prog ? 0 : noProgress + 1;
  // 回填上一杆是否取得进展（决定它能否被再次选中）
  if (lastKey) {
    const e = shotLog.get(lastKey);
    if (e) e.prog = prog;
    else shotLog.set(lastKey, { prog });
  }
}

// ---------- 消息处理 ----------
function handle(r) {
  if (r.type === 'init') {
    cfg = r.config;
    if (r.terrain) parseTerrain(r.terrain);
    hole = r.hole; actor = r.actor; objects = r.objects || [];
    strokes = r.strokes || 0; flyLeft = r.fly ?? 1;
    loadCalib(); noProgress = 0; lastBugMove = null;
    shotLog.clear(); lastKey = null; fbCount.n = 0;
    process.stderr.write(`init ${hole.id} goal=${hole.goal} wind=${hole.wind} tee=[${hole.tee?.x0},${hole.tee?.x1}] fly=${flyLeft}\n`);
    return { ok: true };
  }
  if (r.type === 'exam') {
    const preds = (r.questions || []).map(q => {
      const u = U_A * q.power + U_B;
      const th = q.angle * DEG;
      const vx = Math.cos(th) * q.dir * u, vy = -Math.sin(th) * u;
      const ax = WIND_K * q.wind;
      let px = q.x, py = 780 - MUZZLE_H;
      for (let t = 0.0005; t < 200; t += 0.0005) {
        const x = q.x + vx * t + 0.5 * ax * t * t;
        const y = 780 - MUZZLE_H + vy * t + 0.5 * t * t;
        if (y >= 780) {
          const f = (780 - py) / (y - py || 1e-9);
          return { id: q.id, x: +(px + (x - px) * f).toFixed(1), y: 780 };
        }
        px = x; py = y;
      }
      return { id: q.id, x: +px.toFixed(1), y: 780 };
    });
    return { predictions: preds };
  }
  if (r.type === 'turn') {
    if (r.terrain && !grid) parseTerrain(r.terrain);
    hole = r.hole; actor = r.actor; objects = r.objects || [];
    lastResult = r.lastResult; strokes = r.strokes || 0; flyLeft = r.fly ?? flyLeft;
    if (lastResult && lastResult.action && lastResult.action.fly) flyLeft = Math.max(0, flyLeft - 1);
    analyzeLast();
    return decide();
  }
  throw new Error('unknown message ' + r.type);
}

const rl = createInterface({ input: process.stdin, terminal: false });
for await (const line of rl) {
  let r;
  try { r = JSON.parse(line); } catch { continue; }
  let reply;
  try { reply = handle(r); }
  catch (e) {
    process.stderr.write('ERR ' + (e && e.stack) + '\n');
    reply = r.type === 'init' ? { ok: true } : safeFallback();
  }
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
saveMemory();
