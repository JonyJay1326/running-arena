// 弹弹高尔夫 Bot —— 基于试射拟合的物理模型，数值反解瞄准 + 目标评分 + 落点反馈纠偏。
import { createInterface } from 'node:readline';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import {
  PARAMS, decodeTerrain, shotImpacts, flatLanding, activeObjects,
  objGeom, isSolid, groundY, blocked, windAx, velocity, muzzle,
  W, HGT, CELL, DT, YREF,
} from './physics.mjs';

const clog = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
const MEM = 'bot/memory';
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// ---------- 跨洞记忆：累积落点偏差，用于全局 power 校正 ----------
function loadMem() {
  try { return JSON.parse(readFileSync(MEM + '/learn.json', 'utf8')); }
  catch { return { biasX: 0, biasY: 0, n: 0 }; }
}
function saveMem(m) {
  try { if (!existsSync(MEM)) mkdirSync(MEM, { recursive: true }); writeFileSync(MEM + '/learn.json', JSON.stringify(m)); }
  catch (e) { clog('mem write fail', e.message); }
}

// 每洞进程内状态
let CFG = null;
let holeState = null; // { prevPredicted, bias:{dx,dy}, n, lastStand, lastShot }

function activeSet(obs) {
  return activeObjects(obs.objects || []);
}

// 计算某 stand 处的脚底高度（用于移动站位）。多层地形时取靠近当前脚底、且满足 minFeet 的表面。
function footAt(t, x, curFoot, minFeet) {
  const cx = clamp(Math.floor(x / CELL), 0, t.w - 1);
  const surfaces = [];
  for (let cy = 0; cy < t.h; cy++) {
    const solid = t.grid[cy * t.w + cx] === 1;
    const above = cy > 0 ? t.grid[(cy - 1) * t.w + cx] === 1 : false;
    if (solid && !above) surfaces.push(cy * CELL);
  }
  if (!surfaces.length) return curFoot;
  let cand = surfaces;
  if (minFeet != null) {
    const f = surfaces.filter(y => y >= minFeet - 1);
    if (f.length) cand = f;
  }
  let best = cand[0];
  for (const y of cand) if (Math.abs(y - curFoot) < Math.abs(best - curFoot)) best = y;
  return best;
}

// 回收区里一个"可让 Bug 静止且圆心在区域内"的落脚点
function recycleAim(t, recycle, bug) {
  if (!recycle) return null;
  const r = bug.radius ?? 14;
  let best = null, bd = Infinity;
  const bx = bug.x, by = bug.y - r;
  for (let x = recycle.x0 + 6; x <= recycle.x1 - 6; x += 6) {
    const ys = groundY(t, x);
    if (ys >= HGT) continue;                 // 该列无支撑（空洞），Bug 会掉出地图
    const cy = ys - r;                       // Bug 静止时圆心
    if (cy < recycle.y0 || cy > recycle.y1) continue;
    if (isSolid(t, x, cy)) continue;         // 该处被地形填实，站不住
    const d = Math.hypot(x - bx, cy - by);
    if (d < bd) { bd = d; best = { cx: x, cy }; }
  }
  if (best) return best;
  // 区域被地形完全覆盖、找不到严格落脚点时，至少朝区域中心方向推（不禁用 Bug 推进评分）
  return { cx: (recycle.x0 + recycle.x1) / 2, cy: (recycle.y0 + recycle.y1) / 2 };
}

const ATT = { single: 120, burst: 65, spread: 55, dig: 30 };
const RAD = { single: 32, burst: 26, spread: 24, dig: 60 };
const DMGR = 30;

// 评分：爆炸对各目标物体的效果（越大越好）
function evaluate(impacts, kind, obs, act, t, aim) {
  const goal = obs.hole.goal;
  const wantTok = goal === 'tokens' || goal === 'both';
  const wantBug = goal === 'bug' || goal === 'both';
  const recycle = obs.hole.recycle;
  let sc = 0;
  const dmg = {};   // id -> 累计伤害
  const prox = {};  // id -> 软 proximity 峰值（按物体取各爆炸最大值，避免连发被重复加成）
  for (const E of impacts) {
    if (E.off) continue;
    for (const o of act) {
      const g = objGeom(o);
      const d = Math.hypot(E.x - g.cx, E.y - g.cy);
      if (o.type !== 'chicken') prox[o.id] = Math.max(prox[o.id] || 0, 6 * Math.max(0, 1 - d / 100));
      if (o.type === 'chicken') {
        // 炸到人质：重罚（+1 杆且这一杆白费）
        if (d < DMGR + 8 && !blocked(t, E.x, E.y - 2, g.cx, g.cy)) sc -= 260;
        continue;
      }
      if (o.type === 'bug') continue;
      if (d > DMGR + 6) continue;
      let val = 0;
      if (E.objectId === o.id) val = ATT[kind];            // 直接命中
      else if (!blocked(t, E.x, E.y - 2, g.cx, g.cy)) val = ATT[kind] * Math.max(0, 1 - d / (DMGR + 6));
      dmg[o.id] = (dmg[o.id] || 0) + val;
    }
  }
  for (const k in prox) sc += prox[k];
  if (wantTok) {
    for (const o of act) {
      if (o.type === 'bug' || o.type === 'chicken') continue;
      const hp = o.hp ?? (o.type === 'token' ? 1 : 240);
      const maxHp = o.maxHp ?? hp;
      const got = dmg[o.id] || 0;
      if (o.type === 'token') { if (got > 0) sc += 100; }
      else {
        if (got >= hp) sc += 100;
        else sc += (got / Math.max(1, maxHp)) * 55;
      }
    }
  }
  // Bug 推进：爆炸在 Bug 背离回收区的一侧、且近，则把它推向回收区
  if (wantBug) {
    const bug = act.find(o => o.type === 'bug');
    if (bug && aim) {
      const g = objGeom(bug);
      const dx = aim.cx - g.cx, dy = aim.cy - g.cy;
      const dl = Math.hypot(dx, dy) || 1;
      const toR = { x: dx / dl, y: dy / dl };
      for (const E of impacts) {
        if (E.off) continue;
        const vx = g.cx - E.x, vy = g.cy - E.y;
        const d = Math.hypot(vx, vy);
        if (d > 78 || d < 2) continue;
        const px = vx / d, py = vy / d;
        const align = px * toR.x + py * toR.y;         // +1 表示推向回收区
        const close = Math.max(0, 1 - d / 78);
        sc += align * close * 130;
        const nx = g.cx + px * close * 90;
        if (nx < 25 || nx > W - 25) sc -= 70;         // 别把 Bug 推出地图（罚杆重置）
        sc += 5 * Math.max(0, 1 - d / 110);
      }
    }
  }
  // 弹种代价：简单低血目标优先 single（连发的后续子弹在靶碎后会继续前飞，
  // 可能误炸靶后的人质或乱推物体，属未建模的连带风险）；高血/推 Bug 时 burst 的
  // 额外伤害/推力收益远超此代价，仍会被选中。
  const KIND_COST = { single: 0, spread: 2, burst: 4, dig: 1 };
  sc -= KIND_COST[kind] || 0;
  return sc;
}

// 生成候选站位
function stands(tee, teeActive, actorX, t, curFoot) {
  if (!teeActive || !tee) return [{ x: actorX, foot: curFoot }];
  const set = new Set([actorX]);
  const x0 = tee.x0, x1 = tee.x1;
  for (const f of [0, 0.25, 0.5, 0.75, 1]) set.add(Math.round(x0 + f * (x1 - x0)));
  const out = [];
  for (const x of set) {
    if (x < x0 - 1 || x > x1 + 1) continue;
    const foot = footAt(t, x, curFoot, tee.minFeet);
    // 脚下需有地面支撑
    if (!isSolid(t, x, foot + 2) && !isSolid(t, x, foot + CELL)) {
      // 允许（多层/斜坡），但记录
    }
    out.push({ x, foot });
  }
  if (!out.length) out.push({ x: actorX, foot: curFoot });
  return out;
}

function simulate(st, dir, kind, angle, power, ga, wind, t, act) {
  return shotImpacts(PARAMS, {
    footX: st.x, footY: st.foot, groundAngle: ga,
    angle, power, dir, kind, wind,
  }, t, act, DT * 2);   // 搜索用较粗步长加速
}

// 主决策
function planTurn(obs) {
  const t = decodeTerrain(obs.terrain);
  const act = activeSet(obs);
  const hole = obs.hole;
  const wind = hole.wind || 0;
  const ga = obs.actor.groundAngle || 0;
  const footOffset = (CFG && CFG.BODY && CFG.BODY.footOffset) || 12;
  const curFoot = obs.actor.y + footOffset;
  const actorX = obs.actor.x;

  // 反馈纠偏：只用第一发子弹的落点（其余子弹受"物体被推动/地形被炸开"影响，
  // 落点分散，平均偏差是噪声而非物理模型误差），且做阻尼与限幅。
  const mem = loadMem();
  let observed = null;
  if (holeState && holeState.prevPredicted && obs.lastResult && obs.lastResult.impacts) {
    const A = obs.lastResult.impacts, P = holeState.prevPredicted;
    if (A.length && P.length && A[0].x != null && P[0] != null) {
      observed = { dx: A[0].x - P[0].x, dy: A[0].y - P[0].y };
      clog('feedback raw(first)', observed.dx.toFixed(1), observed.dy.toFixed(1));
    }
  }
  const within = observed
    ? { dx: clamp(observed.dx * 0.6, -45, 45), dy: clamp(observed.dy * 0.6, -45, 45) }
    : { dx: 0, dy: 0 };
  // 跨洞学习：把跨洞累积的系统偏差作为小先验叠加到本洞反馈（首杆无洞内反馈时尤其有用）。
  const bias = {
    dx: clamp(within.dx + 0.35 * mem.biasX, -45, 45),
    dy: clamp(within.dy + 0.35 * mem.biasY, -45, 45),
  };
  if (observed) {
    mem.n += 1;
    mem.biasX = mem.biasX * 0.85 + observed.dx * 0.15;
    mem.biasY = mem.biasY * 0.85 + observed.dy * 0.15;
    saveMem(mem);
  }

  const bug = act.find(o => o.type === 'bug');
  const aim = (hole.goal !== 'tokens' && bug) ? recycleAim(t, hole.recycle, bug) : null;
  const stList = stands(hole.tee, hole.teeActive, actorX, t, curFoot);

  // 目标候选（用于粗搜种子评分的软梯度已在 evaluate 内）
  const kinds = ['single', 'burst', 'spread', 'dig'];
  const ANG = [15, 24, 33, 42, 51, 60, 69, 78, 85];
  const POW = [24, 38, 52, 66, 80, 92, 100];

  // 应用偏差到落点（纠偏）
  const applyBias = imps => imps.map(E => ({ ...E, x: E.x + bias.dx, y: E.y + bias.dy }));

  let best = null;
  const consider = (st, dir, kind, angle, power) => {
    const imps = simulate(st, dir, kind, angle, power, ga, wind, t, act);
    const sc = evaluate(applyBias(imps), kind, obs, act, t, aim) - Math.abs(st.x - actorX) * 0.02;
    if (!best || sc > best.sc) best = { st, dir, kind, angle, power, sc, imps };
    return sc;
  };

  // 粗搜
  const seeds = [];
  for (const st of stList)
    for (const dir of [1, -1])
      for (const kind of kinds)
        for (const angle of ANG)
          for (const power of POW) {
            const imps = simulate(st, dir, kind, angle, power, ga, wind, t, act);
            const sc = evaluate(applyBias(imps), kind, obs, act, t, aim) - Math.abs(st.x - actorX) * 0.02;
            seeds.push({ st, dir, kind, angle, power, sc });
          }
  seeds.sort((a, b) => b.sc - a.sc);
  // 取前若干做细化，保证 dir/站位有多样性
  const picked = [];
  for (const s of seeds) {
    if (picked.length >= 26) break;
    picked.push(s);
  }
  // 细化：在种子附近微调 angle / power，并顺便评估多种弹种
  for (const s of picked) {
    for (const kind of kinds) {
      for (let da = -9; da <= 9; da += 3)
        for (let dp = -15; dp <= 15; dp += 5) {
          const angle = clamp(s.angle + da, 5, 85), power = clamp(s.power + dp, 12, 100);
          consider(s.st, s.dir, kind, angle, power);
        }
    }
  }

  if (!best) {
    // 兜底：合法动作
    best = { st: { x: actorX }, dir: 1, kind: 'single', angle: 45, power: 60, imps: [], sc: 0 };
  }

  // 记录本杆预测落点供下杆反馈
  holeState = {
    prevPredicted: best.imps.map(E => ({ x: E.x, y: E.y })),
    bias,
    lastShot: { kind: best.kind, angle: best.angle, power: best.power, dir: best.dir },
    lastStand: best.st.x,
  };
  clog('decision', JSON.stringify({ kind: best.kind, angle: best.angle, power: best.power, dir: best.dir, stand: best.st.x, sc: best.sc.toFixed(1) }));

  // 组装动作
  const attack = { kind: best.kind, angle: Math.round(best.angle), power: Math.round(best.power), dir: best.dir };
  const reply = { attack };
  if (hole.teeActive && Math.abs(best.st.x - actorX) > 1) reply.moveX = Math.round(best.st.x);
  return reply;
}

function respond(r) {
  switch (r.type) {
    case 'init':
      CFG = r.config || CFG;
      holeState = null;
      clog('init hole', r.hole && r.hole.id, 'wind', r.hole && r.hole.wind, 'goal', r.hole && r.hole.goal);
      return { ok: true };
    case 'exam': {
      const ground = r.ground ?? 780;
      const qs = r.questions || [];
      const predictions = qs.map(q => {
        const p = flatLanding(PARAMS, { x: q.x, angle: q.angle, power: q.power, dir: q.dir, wind: q.wind || 0, ground });
        return { id: q.id, x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10 };
      });
      return { predictions };
    }
    case 'turn': {
      try { return planTurn(r); }
      catch (e) {
        clog('planTurn ERROR', e.stack || e.message);
        return { attack: { kind: 'single', angle: 45, power: 60, dir: 1 } };
      }
    }
    default:
      clog('unknown msg', r.type);
      return {};
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  let r;
  try { r = JSON.parse(line); } catch { continue; }
  const reply = respond(r);
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
