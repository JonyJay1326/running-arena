// 弹弹高尔夫 Bot —— 第二轮。
// 第一轮复盘结论：物理模型本身在复杂地形上仍准（诊断复算落点与实测差几像素），
// 失败源于决策层：被污染的跨洞 bias 把评分落点系统性带偏、缺少防复读、
// 出界落点不罚、人质半径过小。本轮只针对决策层做通用修正，不认洞、不套用历史动作。
import { createInterface } from 'node:readline';
import {
  PARAMS, decodeTerrain, shotImpacts, flatLanding, activeObjects,
  objGeom, isSolid, groundY, blocked, W, HGT, CELL, DT,
} from './physics.mjs';

const clog = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// 每洞进程内状态：只用于"防复读"，不携带任何洞序/坐标指纹/预设动作。
let CFG = null;
let holeState = null; // { badSigs:Set<string>, lastSig:string }

function activeSet(obs) { return activeObjects(obs.objects || []); }

// 某 stand 处脚底高度：取靠近当前脚底、且满足 minFeet 的支撑面。
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

// 回收区里一个"可让 Bug 静止且圆心在区域内"的落脚点；否则退回区域中心。
function recycleAim(t, recycle, bug) {
  if (!recycle) return null;
  const r = bug.radius ?? 14;
  let best = null, bd = Infinity;
  const bx = bug.x, by = bug.y - r;
  for (let x = recycle.x0 + 4; x <= recycle.x1 - 4; x += 4) {
    const ys = groundY(t, x);
    if (ys >= HGT) continue;
    const cy = ys - r;
    if (cy < recycle.y0 || cy > recycle.y1) continue;
    if (isSolid(t, x, cy)) continue;
    const d = Math.hypot(x - bx, cy - by);
    if (d < bd) { bd = d; best = { cx: x, cy }; }
  }
  return best || { cx: (recycle.x0 + recycle.x1) / 2, cy: (recycle.y0 + recycle.y1) / 2 };
}

const ATT = { single: 120, burst: 65, spread: 55, dig: 30 };
const DMGR = 30;
const CHICKEN_R = 52;   // 第一轮洞07：距鸡 39px 仍被判炸到 → 放大留余量
const OFF_PENALTY = 80;   // 别太重：三叉里 1 发出界、另 2 发碎靶仍是好球；但整杆全出界会被强压
const CHICKEN_PENALTY = 900;

// 评分：爆炸对各目标物体的效果（越大越好）。
// proximity 半径放宽到 400，避免"全部候选都得 0"的平坦区导致退化复读。
function evaluate(impacts, kind, obs, act, t, aim) {
  const goal = obs.hole.goal;
  const wantTok = goal === 'tokens' || goal === 'both';
  const wantBug = goal === 'bug' || goal === 'both';
  let sc = 0;
  const dmg = {}, prox = {};
  for (const E of impacts) {
    // 出界落点：整发无效且浪费一杆，明确重罚（第一轮洞03/04 大量飞出右界）。
    if (E.off) { sc -= OFF_PENALTY; continue; }
    for (const o of act) {
      const g = objGeom(o);
      const d = Math.hypot(E.x - g.cx, E.y - g.cy);
      if (o.type !== 'chicken') prox[o.id] = Math.max(prox[o.id] || 0, 12 * Math.max(0, 1 - d / 400));
      if (o.type === 'chicken') {
        if (d < CHICKEN_R && !blocked(t, E.x, E.y - 2, g.cx, g.cy)) sc -= CHICKEN_PENALTY;
        continue;
      }
      if (o.type === 'bug') continue;
      if (d > DMGR + 6) continue;
      let val = 0;
      if (E.objectId === o.id) val = ATT[kind];
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
  // 遮挡开路：掘进弹坑半径 60，目标是被地形挡住、直接命中够不到时，
  // 逐杆炸同一段墙能凿开通道。仅当落点"打在地形上且离目标较远（非直接可命中）"
  // 才给小额奖励，权重刻意压低，避免在可直接命中的洞抢走 burst/single。
  if (kind === 'dig') {
    const tg = act.filter(o => o.type !== 'chicken').map(objGeom);
    for (const E of impacts) {
      if (E.off || E.objectId != null) continue;
      let near = Infinity;
      for (const g of tg) near = Math.min(near, Math.hypot(E.x - g.cx, E.y - g.cy));
      if (near > 48 && near < 320) sc += 7 * (1 - near / 320);
    }
  }
  // Bug 推进：爆炸在 Bug 背离回收区一侧且近，则推向回收区。
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
        if (d > 90 || d < 2) continue;
        const px = vx / d, py = vy / d;
        const align = px * toR.x + py * toR.y;
        const close = Math.max(0, 1 - d / 90);
        sc += align * close * 130;
        const nx = g.cx + px * close * 90;
        if (nx < 30 || nx > W - 30) sc -= 400; // 别把 Bug 推出地图（会罚杆并重置）
        sc += 12 * Math.max(0, 1 - d / 220);
      }
    }
  }
  const KIND_COST = { single: 0, spread: 2, burst: 4, dig: 1 };
  sc -= KIND_COST[kind] || 0;
  return sc;
}

function stands(tee, teeActive, actorX, t, curFoot) {
  if (!teeActive || !tee) return [{ x: actorX, foot: curFoot }];
  const set = new Set([actorX]);
  const x0 = tee.x0, x1 = tee.x1;
  for (const f of [0, 0.25, 0.5, 0.75, 1]) set.add(Math.round(x0 + f * (x1 - x0)));
  const out = [];
  for (const x of set) {
    if (x < x0 - 1 || x > x1 + 1) continue;
    out.push({ x, foot: footAt(t, x, curFoot, tee.minFeet) });
  }
  if (!out.length) out.push({ x: actorX, foot: curFoot });
  return out;
}

function simulate(st, dir, kind, angle, power, ga, wind, t, act) {
  return shotImpacts(PARAMS, {
    footX: st.x, footY: st.foot, groundAngle: ga, angle, power, dir, kind, wind,
  }, t, act, DT * 2);
}

// 粗粒度签名：角度/力度按桶归并，让"轻微改参数但同样无效"的复读也被识别。
const sigOf = (kind, angle, power, dir, stx) =>
  `${kind}|${Math.round(angle / 7)}|${Math.round(power / 8)}|${dir}|${Math.round(stx / 40)}`;

// 兜底"寻的"：所有候选得分都 ≤0 时，挑一个首弹落点离最近目标最近的合法动作，
// 保证每杆都朝某个目标打，而不是退回最小力度贴脚射击。
function seekBest(obs, t, act, stList, ga, wind, actorX) {
  const targets = act.filter(o => o.type !== 'chicken').map(o => objGeom(o));
  let best = null;
  for (const st of stList)
    for (const dir of [1, -1])
      for (const kind of ['single', 'burst'])
        for (let angle = 12; angle <= 84; angle += 6)
          for (let power = 40; power <= 100; power += 10) {
            const imps = simulate(st, dir, kind, angle, power, ga, wind, t, act);
            const E = imps[0];
            if (!E || E.off) continue;
            let md = Infinity;
            for (const g of targets) md = Math.min(md, Math.hypot(E.x - g.cx, E.y - g.cy));
            const sig = sigOf(kind, angle, power, dir, st.x);
            const penalty = (holeState && holeState.badSigs.has(sig)) ? 500 : 0;
            const sc = -md + penalty - Math.abs(st.x - actorX) * 0.01;
            if (!best || sc > best.sc) best = { st, dir, kind, angle, power, sc, imps };
          }
  return best;
}

function planTurn(obs) {
  const t = decodeTerrain(obs.terrain);
  const act = activeSet(obs);
  const hole = obs.hole;
  const wind = hole.wind || 0;
  const ga = obs.actor.groundAngle || 0;
  const footOffset = (CFG && CFG.BODY && CFG.BODY.footOffset) || 12;
  const curFoot = obs.actor.y + footOffset;
  const actorX = obs.actor.x;

  // 防复读：仅当上一杆"零物体变化、零罚杆"时把该动作签名记入 badSigs；
  // 有进展即清空，允许有效动作（如高血目标连发）重复。不记录任何洞序/坐标指纹。
  if (!holeState) holeState = { badSigs: new Set(), lastSig: null };
  const lr = obs.lastResult;
  // 仅在"目标附近被炸开"时算作开路进展：通用物理关系，避免把无意义的贴脚挖地当进展。
  let channelOpened = false;
  if (holeState.prevGrid) {
    const tg = act.filter(o => o.type !== 'chicken').map(objGeom);
    const g0 = holeState.prevGrid;
    for (let cy = 0; cy < t.h && !channelOpened; cy++) {
      for (let cx = 0; cx < t.w; cx++) {
        const idx = cy * t.w + cx;
        if (g0[idx] === 1 && t.grid[idx] === 0) { // 这格刚被炸空
          const px = cx * CELL + CELL / 2, py = cy * CELL + CELL / 2;
          for (const g of tg) {
            if (Math.hypot(px - g.cx, py - g.cy) < 200) { channelOpened = true; break; }
          }
          if (channelOpened) break;
        }
      }
    }
  }
  holeState.prevGrid = Uint8Array.from(t.grid);
  if (holeState.lastSig && lr) {
    const changes = lr.objectChanges || [];
    const progressed = changes.length > 0 || channelOpened;
    if (progressed) holeState.badSigs.clear();
    else {
      holeState.badSigs.add(holeState.lastSig);
      if (holeState.badSigs.size > 16) {
        const first = holeState.badSigs.values().next().value;
        holeState.badSigs.delete(first);
      }
    }
  }

  const bug = act.find(o => o.type === 'bug');
  const aim = (hole.goal !== 'tokens' && bug) ? recycleAim(t, hole.recycle, bug) : null;
  const stList = stands(hole.tee, hole.teeActive, actorX, t, curFoot);

  const kinds = ['single', 'burst', 'spread', 'dig'];
  const ANG = [15, 24, 33, 42, 51, 60, 69, 78, 85];
  const POW = [30, 42, 54, 66, 78, 90, 100];

  let best = null;
  const consider = (st, dir, kind, angle, power) => {
    const imps = simulate(st, dir, kind, angle, power, ga, wind, t, act);
    const sig = sigOf(kind, angle, power, dir, st.x);
    let sc = evaluate(imps, kind, obs, act, t, aim) - Math.abs(st.x - actorX) * 0.02;
    if (holeState.badSigs.has(sig)) sc -= 350;
    if (!best || sc > best.sc) best = { st, dir, kind, angle, power, sc, imps };
  };

  // 粗搜 + 收集种子（同一批评分）
  const seeds = [];
  for (const st of stList)
    for (const dir of [1, -1])
      for (const kind of kinds)
        for (const angle of ANG)
          for (const power of POW) {
            const imps = simulate(st, dir, kind, angle, power, ga, wind, t, act);
            const sig = sigOf(kind, angle, power, dir, st.x);
            let sc = evaluate(imps, kind, obs, act, t, aim) - Math.abs(st.x - actorX) * 0.02;
            if (holeState.badSigs.has(sig)) sc -= 350;
            seeds.push({ st, dir, kind, angle, power, sc });
            if (!best || sc > best.sc) best = { st, dir, kind, angle, power, sc, imps };
          }
  // 细化：对最优种子局部微调，并交叉评估弹种
  seeds.sort((a, b) => b.sc - a.sc);
  for (const s of seeds.slice(0, 22)) {
    for (const kind of kinds)
      for (let da = -9; da <= 9; da += 3)
        for (let dp = -12; dp <= 12; dp += 4) {
          const angle = clamp(s.angle + da, 8, 85), power = clamp(s.power + dp, 24, 100);
          consider(s.st, s.dir, kind, angle, power);
        }
  }

  // 全负分/平坦区 → 寻的兜底，避免退化为 ang6/pow12 贴脚复读
  if (!best || best.sc <= 0) {
    const seek = seekBest(obs, t, act, stList, ga, wind, actorX);
    if (seek && (!best || seek.sc > best.sc + 1)) best = seek;
  }
  if (!best) best = { st: { x: actorX }, dir: 1, kind: 'single', angle: 45, power: 70, imps: [], sc: 0 };

  holeState.lastSig = sigOf(best.kind, best.angle, best.power, best.dir, best.st.x);
  clog('decision', JSON.stringify({ kind: best.kind, angle: best.angle, power: best.power, dir: best.dir, stand: best.st.x, sc: Number(best.sc.toFixed(1)), bad: holeState.badSigs.size }));

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
      clog('init wind', r.hole && r.hole.wind, 'goal', r.hole && r.hole.goal);
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
        return { attack: { kind: 'single', angle: 45, power: 70, dir: 1 } };
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
