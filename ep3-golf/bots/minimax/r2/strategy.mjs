// 决策模块：根据观测选择下一杆动作。
//
// 实测机制（真实引擎反馈，详见 NOTES.md）：
//  - 炮弹直接命中对象 → 该对象吃满该弹种伤害（不超过剩余 hp）。
//  - 溅射按距离线性衰减，半径 30；中间隔着地形则不受伤害。
//  - burst：三发同一角度、间隔 0.8s，同一点累计 3×65=195 伤害（可一发打掉 hp<=195 的 token）。
//  - spread：三叉同时，角度 -6/0/+6，每发 55。
//  - 尖叫鸡受到任何伤害 → 罚 1 杆并退场 → 所有候选动作必须排除。
//  - Bug 不会被打坏；爆炸把它沿"背离爆炸点"方向推开并使其滑行。
//    实测：single 在距 Bug 中心 d 处爆炸 → 位移 ≈ 35×(34−d) 像素（12<=d<=34）。
//    d<10 时爆炸会挖空 Bug 脚下地形（反常且危险），故回避。
import {
  G, MUZZLE_H, WIND_A,
  decodeTerrain, surfaceY, objCenter, isGone, inRect,
  solveToPoint, simulate,
} from './physics.mjs';

export function makeEnv(obs) {
  const T = decodeTerrain(obs.terrain);
  const cfg = obs.config || {};
  const kinds = {};
  for (const [k, v] of Object.entries(cfg.ATTACKS || {})) {
    kinds[k] = {
      dmg: v.damage, radius: v.radius,
      offsets: v.offsets || [0], ticks: v.ticks || [0],
    };
  }
  const actor = obs.actor || { x: 0, y: 0, groundAngle: 0 };
  return {
    T, objects: obs.objects || [],
    wind: (obs.hole && obs.hole.wind) || 0,
    ga: actor.groundAngle || 0,
    footOffset: (cfg.BODY && cfg.BODY.footOffset) || 12,
    muzzleHeight: (cfg.BODY && cfg.BODY.muzzleHeight) || 35,
    kinds, cfg,
  };
}

/** 坡向：rise>0 表示右侧地面更高 */
function slopeInfo(env, x) {
  const yl = surfaceY(env.T, x - 24), yr = surfaceY(env.T, x + 24);
  if (yl == null || yr == null) return { rise: 0, deg: 0 };
  const dy = yl - yr;
  return { rise: dy, deg: Math.atan2(dy, 48) * 180 / Math.PI };
}

/** 有效地面倾角：引擎 groundAngle 的符号约定未公开，用地形坡向推断 */
export function effectiveGa(env, x) {
  const local = slopeInfo(env, x);
  const eng = Math.abs(env.ga || 0);
  if (Math.abs(local.deg) < 0.5) return 0;
  return (local.rise > 0 ? 1 : -1) * eng;
}

/** 炮口位置：feet + muzzleHeight * 身体法线 */
export function muzzleAt(env, x, feetY, ga) {
  const rad = ga * Math.PI / 180;
  return { mx: x - MUZZLE_H * Math.sin(rad), my: feetY - MUZZLE_H * Math.cos(rad) };
}

export function candidateStands(env, obs) {
  const out = [{ x: obs.actor.x, feetY: obs.actor.y + env.footOffset, cur: true }];
  const tee = obs.hole.tee || {};
  if (obs.hole.teeActive && tee.x0 != null && tee.x1 != null) {
    const minFeet = tee.minFeet;
    const n = 6;
    const step = (tee.x1 - tee.x0) / n;
    for (let i = 0; i <= n; i++) {
      const x = Math.round(tee.x0 + step * i);
      if (Math.abs(x - obs.actor.x) < 8) continue;
      const sy = surfaceY(env.T, x);
      if (sy == null) continue;
      if (minFeet != null && sy < minFeet) continue;
      const yl = surfaceY(env.T, x - 20), yr = surfaceY(env.T, x + 20);
      if (yl == null || yr == null) continue;
      if (Math.abs(yl - sy) > 6 || Math.abs(yr - sy) > 6) continue;
      out.push({ x, feetY: sy, cur: false });
    }
  }
  return out;
}

function cloneTerrain(T) {
  return { grid: Uint8Array.from(T.grid), W: T.W, H: T.H, cell: T.cell };
}

function applyCrater(T, px, py, radius) {
  const r = radius;
  const cx0 = Math.max(0, Math.floor((px - r) / T.cell));
  const cx1 = Math.min(T.W - 1, Math.floor((px + r) / T.cell));
  const cy0 = Math.max(0, Math.floor((py - r) / T.cell));
  const cy1 = Math.min(T.H - 1, Math.floor((py + r) / T.cell));
  for (let cy = cy0; cy <= cy1; cy++) {
    for (let cx = cx0; cx <= cx1; cx++) {
      const x = cx * T.cell + T.cell / 2, y = cy * T.cell + T.cell / 2;
      if (Math.hypot(x - px, y - py) <= r && y >= py - r * 0.4) T.grid[cy * T.W + cx] = 0;
    }
  }
}

function supportAt(T, x, baseY) {
  const cx = Math.floor(x / T.cell);
  if (cx < 0 || cx >= T.W) return null;
  for (let cy = Math.max(0, Math.floor(baseY / T.cell)); cy < T.H; cy++) {
    if (T.grid[cy * T.W + cx]) return cy * T.cell;
  }
  return null;
}

function losBlocked(T, x1, y1, x2, y2) {
  for (let i = 1; i < 6; i++) {
    const f = i / 6;
    const cx = Math.floor((x1 + (x2 - x1) * f) / T.cell);
    const cy = Math.floor((y1 + (y2 - y1) * f) / T.cell);
    if (cx < 0 || cx >= T.W || cy < 0 || cy >= T.H) continue;
    if (T.grid[cy * T.W + cx]) return true;
  }
  return false;
}

/** 爆炸推动位移（模型：位移 = A×(R−d)），d 为爆炸点到对象中心的距离 */
export function predictPush(model, bx, by, ix, iy) {
  const d = Math.hypot(ix - bx, iy - by);
  const ux = d > 0.01 ? (bx - ix) / d : 0;
  const uy = d > 0.01 ? (by - iy) / d : 0;
  if (d >= model.R) return { dx: 0, dy: 0, d, ux, uy };
  const m = model.A * (model.R - d);
  return { dx: ux * m, dy: uy * m, d, ux, uy };
}
/**
 * 模拟一发（可能多发）炮弹，返回各发落点。
 * burst：三发顺序发射（0.8s 间隔），前一发可能打碎物体/挖空地形，后一发路径随之改变。
 * spread：三发同时，角度 -6/0/+6。
 */
export function simulateShot(env, shot) {
  const A = env.kinds[shot.kind] || { dmg: 120, radius: 32, offsets: [0], ticks: [0] };
  const offsets = A.offsets || [0];
  const ticks = A.ticks || [0];
  const impacts = [];
  if (offsets.length > 1 && ticks.length === 1) {
    // 同时三叉
    for (const off of offsets) {
      impacts.push(simulate({ ...env, T: env.T, objects: env.objects },
        { ...shot, angle: shot.angle + off }));
    }
    return impacts;
  }
  // 顺序多发
  const T = cloneTerrain(env.T);
  const objs = env.objects.map(o => ({ ...o }));
  const dmgSoFar = new Map();
  for (let i = 0; i < ticks.length; i++) {
    const r = simulate({ ...env, T, objects: objs }, { ...shot, angle: shot.angle + offsets[0] });
    impacts.push(r);
    if (r.hit === 'object' && r.objId != null) {
      // 累计伤害：只有 hp 打空后物体才退场，后面的炮弹才会穿过去
      const o = objs.find(q => q.id === r.objId);
      if (o) {
        const hp = o.hp == null ? Infinity : o.hp;
        const already = dmgSoFar.get(r.objId) || 0;
        const applied = Math.min(A.dmg, Math.max(0, hp - already));
        if (applied > 0) dmgSoFar.set(r.objId, already + applied);
        if (already + applied >= hp) o.dead = true;
      }
    }
    applyCrater(T, r.x, r.y, A.radius);
  }
  return impacts;
}

/** 该组落点是否会伤害到尖叫鸡（直接命中，或溅射半径 30 内） */
function hitsChicken(env, impacts) {
  for (const im of impacts) {
    if (im.hit === 'object') {
      const o = env.objects.find(q => q.id === im.objId);
      if (o && o.type === 'chicken') return true;
    }
  }
  for (const c of env.objects) {
    if (c.type !== 'chicken' || isGone(c)) continue;
    const cc = objCenter(c);
    for (const im of impacts) {
      if (Math.hypot(im.x - cc.x, im.y - cc.y) < 30) return true;
    }
  }
  return false;
}

/** 预测一次射击后的世界状态 */
export function predictOutcome(env, obs, shot, impacts, model) {
  const A = env.kinds[shot.kind] || { dmg: 120, radius: 32 };
  const T = cloneTerrain(env.T);
  const dmgMap = new Map();

  for (const r of impacts) {
    if (r.hit === 'object' && r.objId != null) {
      const o = env.objects.find(q => q.id === r.objId);
      const hpLeft = o && o.hp != null ? Math.max(0, o.hp - (dmgMap.get(r.objId) || 0)) : Infinity;
      const d = Math.min(A.dmg, hpLeft);
      if (d > 0) dmgMap.set(r.objId, (dmgMap.get(r.objId) || 0) + d);
    }
    for (const o of env.objects) {
      const c = objCenter(o);
      const d = Math.hypot(r.x - c.x, r.y - c.y);
      if (d < 30 && d > 0.5) {
        const already = dmgMap.get(o.id) || 0;
        if (already >= A.dmg) continue;
        if (losBlocked(T, r.x, r.y, c.x, c.y)) continue;
        const dm = A.dmg * (1 - d / 30);
        if (dm > 0.5) dmgMap.set(o.id, already + dm);
      }
    }
    applyCrater(T, r.x, r.y, A.radius);
  }

  const res = [];
  let bugPushDir = 0;   // 几何推力方向（水平），用于方向性打分
  let bugPushVec = { ux: 0, uy: 0 };   // 二维推力方向（回收区可能在 Bug 上方/下方）
  for (const o of env.objects) {
    const dmg = dmgMap.get(o.id) || 0;
    const hp = o.hp == null ? 0 : o.hp;
    let broken = isGone(o), fellOut = false;
    let x = o.x, y = o.y;
    if (!broken) {
      if (o.type === 'bug' || o.type === 'pushToken') {
        const c = objCenter(o);
        let best = Infinity, bestDir = 0, bestVec = { ux: 0, uy: 0 };
        for (const r of impacts) {
          const p = predictPush(model, c.x, c.y, r.x, r.y);
          if (p.d < best) { best = p.d; bestDir = Math.sign(p.ux); bestVec = { ux: p.ux, uy: p.uy }; }
          x += p.dx; y += p.dy;
        }
        if (o.type === 'bug' && bestDir !== 0) { bugPushDir = bestDir; bugPushVec = bestVec; }
      }
      const sup = supportAt(T, o.x, o.y - 1);
      if (sup == null) {
        if (o.type === 'bug') fellOut = true;
        else broken = true;
      } else {
        y = sup + (o.type === 'bug' ? (o.radius || 14) : (o.height || 28));
        x = o.x;
      }
    }
    if (!broken && !fellOut && dmg > 0 && hp > 0 && dmg >= hp) broken = true;
    res.push({ id: o.id, type: o.type, broken, fellOut, dmg, hp, x, y, chicken: o.type === 'chicken' });
  }
  return { impacts, res, bugPushDir, bugPushVec };
}

function distToRect(x, y, r) {
  if (!r) return 0;
  const cx = Math.max(r.x0, Math.min(r.x1, x));
  const cy = Math.max(r.y0, Math.min(r.y1, y));
  return Math.hypot(x - cx, y - cy);
}

/** 评分（越大越好） */
function scoreOutcome(env, obs, outcome, goals, tokensLeft) {
  let score = 0;
  const rc = obs.hole.recycle;
  const bug = env.objects.find(o => o.type === 'bug');
  for (const r of outcome.res) {
    if (r.chicken && (r.dmg > 0 || r.broken)) score -= 100000;
    if (r.type === 'token' || r.type === 'bigToken' || r.type === 'pushToken') {
      if (goals.tokens) {
        if (r.broken) score += 600;
        else score += r.dmg * 0.6;   // 不封顶：多打血就该多得���，burst 才有价值
      }
    }
  }
  if (goals.bug && bug) {
    const st = outcome.res.find(r => r.id === bug.id);
    if (st) {
      if (st.fellOut) score -= 6000;
      else {
        const bc = objCenter(bug);
        const cy = st.y - (bug.radius || 14) / 2;
        if (rc && inRect(st.x, cy, rc)) score += 2500;
        const d0 = distToRect(bc.x, bc.y, rc);
        const d1 = distToRect(st.x, cy, rc);
        score += Math.max(-150, (d0 - d1) * 5.0);
        // 推力方向必须朝向回收区：推离目标要重罚，否则「推错方向」会被当成等价选项。
        // 方向是二维的 —— 第一轮洞 03/09 的回收区在 Bug 的下方，只看水平方向会判错。
        if (rc) {
          const tx = Math.max(rc.x0 + 12, Math.min(rc.x1 - 12, bc.x));
          const ty = Math.max(rc.y0 + 12, Math.min(rc.y1 - 12, bc.y));
          let wx = tx - bc.x, wy = ty - bc.y;
          const wl = Math.hypot(wx, wy);
          if (wl > 0.5) {
            wx /= wl; wy /= wl;
            const v = outcome.bugPushVec || { ux: 0, uy: 0 };
            const dot = v.ux * wx + v.uy * wy;
            if (dot > 0.3) score += 150;
            else if (dot < -0.3) score -= 150;
          }
        }
        // 若还有 token 没打碎，推 Bug 的收益要打折：
        // 推 Bug 的位移量级不确定（实测 200~600px 方差大），而打碎 token 是确定的一杆。
        // 先把 token 清掉再推 Bug，可避免两头都差一点而整洞记 par+4。
        if (tokensLeft > 0) score *= 0.15;
      }
    }
  }
  return score;
}

/** 主决策 */
export function chooseShot(env, obs, learn) {
  const model = (learn && learn.model) ? learn.model : { A: 30, R: 34 };
  // 防复读：最近几杆里「完全相同且毫无进展」的动作要重罚。
  // 第一轮洞 06/07/08 连续 4~6 杆用同一组参数撞同一堵墙，白白浪费了 14 杆。
  const recent = (learn && learn.recent) || [];
  const staleCount = (kind, angle, power, dir) => {
    let n = 0;
    for (let i = recent.length - 1; i >= 0; i--) {
      const r = recent[i];
      if (r.kind === kind && r.dir === dir
          && Math.abs(r.angle - angle) < 0.6 && Math.abs(r.power - power) < 1.2) {
        if (r.progress) return n;      // 之前这一组是有效的 → 不算复读
        n++;
        if (n >= 2) break;
      }
    }
    return n;
  };
  const goals = {
    tokens: obs.hole.goal === 'tokens' || obs.hole.goal === 'both',
    bug: obs.hole.goal === 'bug' || obs.hole.goal === 'both',
  };
  const stands = candidateStands(env, obs);
  const aw = WIND_A * env.wind;
  const tokens = env.objects.filter(o =>
    (o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken') && !isGone(o));
  const bug = env.objects.find(o => o.type === 'bug' && !isGone(o));

  // ---- 瞄准点 ----
  const aims = [];
  for (const o of tokens) {
    const c = objCenter(o);
    aims.push({ x: c.x, y: c.y, tag: 'tok', id: o.id, hp: o.hp == null ? 1 : o.hp });
  }
  if (bug && goals.bug) {
    const c = objCenter(bug);
    const rc = obs.hole.recycle;
    // Bug 圆心已在回收区内 → 目标已达成，不要再推（否则白费杆数）
    if (rc && inRect(c.x, c.y, rc)) {
      // 不生成推 Bug 的瞄准点
    } else {
    // 目标：把 Bug 移进回收区（圆心进入区域）
    // 关键：回收区可能相对 Bug 有「上下」位移（第一轮洞 03：Bug y=120，回收区 y=160~260；
    // 洞 09：Bug y=96，回收区 y=376~411）。所以方向必须是二维的，不能只看 x。
    let target = { x: c.x, y: c.y };
    if (rc) {
      target = {
        x: Math.max(rc.x0 + 12, Math.min(rc.x1 - 12, c.x)),
        y: Math.max(rc.y0 + 12, Math.min(rc.y1 - 12, c.y)),
      };
    }
    // 期望推动方向：从 Bug 指向回收区内的最近点
    let dx = target.x - c.x, dy = target.y - c.y;
    if (Math.abs(dx) < 4 && Math.abs(dy) < 4 && rc) {
      // Bug 已在回收区的横坐标范围内，只需垂直方向 → 朝区中心偏一点
      dx = ((rc.x0 + rc.x1) / 2) - c.x;
      if (Math.abs(dx) < 4) dx = 0;
    }
    const dl = Math.hypot(dx, dy);
    const ux = dl > 0.5 ? dx / dl : 0;
    const uy = dl > 0.5 ? dy / dl : 0;
    // 推动方向 = 从爆炸点指向 Bug 中心 ⇒ 爆炸点必须在 Bug 的「反方向」一侧。
    // 实测：爆炸点距 Bug 中心 ≲25px 才会推动；直接命中 Bug 会把它炸出地图（bug-out 罚杆），
    // 所以这里只用「侧面/斜下方爆炸」的方式推，绝不直接命中。
    const last = learn && learn.bugLast;
    // 爆炸距离 → 推动位移。实测：距离越近推得越远（d≈13→648px，d≈20→360px，
    // d≈35→107px，d≈40→139px），斜率约 −25 px/px，噪声大但趋势稳定。
    // 因此用「上一杆的实测」做闭环：需要位移 N、上次在距离 d 处得到 D，
    // 则把爆炸距离调整 (D−N)/25（距离增大 → 位移减小）。
    const need = Math.hypot(target.x - c.x, target.y - c.y);
    let dd = 14;
    if (last && last.disp > 2) {
      const SLOPE = 25;
      if (last.over) dd = last.d + (last.disp - need) / SLOPE;
      else dd = last.d + (last.disp - need) / SLOPE;
      dd = Math.max(10, Math.min(52, dd));
    } else {
      // 没有反馈时按需求估算：需要大位移 → 靠近炸；需要小位移 → 远离炸
      dd = need > 250 ? 13 : (need > 120 ? 20 : (need > 60 ? 32 : 46));
    }
    dd = Math.max(10, Math.min(52, dd));
    const dd0 = dd;
    // 沿期望方向的多个爆炸点（近→远），以及它们的水平镜像（用于反向推回来）。
    for (const [ax, ay, tag] of [
      [c.x - ux * dd, c.y - uy * dd, 'bugPush'],
      [c.x - ux * (dd + 10), c.y - uy * (dd + 10), 'bugPush'],
      [c.x - ux * (dd + 22), c.y - uy * (dd + 22), 'bugPush'],
      [c.x + ux * dd, c.y + uy * dd, 'bugPushRev'],
    ]) {
      // 爆炸点要落在地面上（地表高度），否则炮弹打不到那个点
      const gy = surfaceY(env.T, ax);
      aims.push({ x: ax, y: gy == null ? ay : gy, tag, id: bug.id, dd, ux, uy, targetX: target.x, targetY: target.y });
      // 同一水平点、Bug 自身高度（用于越过平台侧壁贴地爆炸）
      aims.push({ x: ax, y: ay, tag, id: bug.id, dd, ux, uy, targetX: target.x, targetY: target.y });
    }
    }
  }
  if (!aims.length) aims.push({ x: 800, y: 740, tag: 'none' });

  const kinds = Object.keys(env.kinds).length ? Object.keys(env.kinds) : ['single'];
  const cands = [];
  const deadline = Date.now() + 750;

  for (const st of stands) {
    const ga = effectiveGa(env, st.x);
    const { mx, my } = muzzleAt(env, st.x, st.feetY, ga);
    for (const ap of aims) {
      const dir = ap.x >= mx ? 1 : -1;
      const sols = solveToPoint(mx, my, ap.x, ap.y, dir, aw, ga, { step: 2 });
      // 取若干不同弧线：低平弹（快、远）与高抛弹（越过障碍/物体）。
      // 推 Bug 时高抛弹尤其重要——只有越过 Bug 落到另一侧才能往回推。
      const picked = [];
      if (sols.length) {
        const idxs = ap.tag === 'bugPush'
          ? [...Array(Math.min(sols.length, 10)).keys(), sols.length - 1, sols.length - 2]
          : [0, 1, 2, 3, 4];
        for (const i of idxs) {
          if (i < 0 || i >= sols.length) continue;
          const s = sols[i];
          if (s.power < 3) continue;
          if (picked.some(p => Math.abs(p.angle - s.angle) < 1.5)) continue;
          picked.push(s);
          if (picked.length >= 12) break;
        }
      }
      for (const s of picked) {
        for (const kind of kinds) {
          if (Date.now() > deadline && cands.length > 40) break;
          const A = env.kinds[kind];
          // 弹种选择：按目标剩余血量算「几发能打死」，取累计伤害最大的弹种。
          // burst 的三连发累计 3×65=195，是打高血量 token 的正确选择；
          // 旧逻辑用单发伤害比较，会把 burst 排除掉，导致高血量目标永远打不死。
          let kindOk = true;
          if (ap.tag === 'tok' && A) {
            const perShot = A.ticks ? A.ticks.length * A.dmg : A.dmg;
            if (perShot <= 0) kindOk = false;
            else {
              // 累计伤害不足且没有别的弹种能一发打穿 → 仍然允许（多打几发总比不打强）
              const bestPerShot = Math.max(...kinds.map(k => {
                const B = env.kinds[k];
                return B ? (B.ticks ? B.ticks.length * B.dmg : B.dmg) : 0;
              }));
              if (perShot < bestPerShot * 0.6) kindOk = false;  // 明显次优的弹种直接排除
            }
          }
          // 推 Bug 只用 single：spread 的三叉落点分散，方向不可控
          if ((ap.tag === 'bugPush' || ap.tag === 'bugPushRev') && kind !== 'single') kindOk = false;
          if (!kindOk) continue;
          const impacts = simulateShot(env, { kind, angle: s.angle, power: s.power, dir: s.dir, mx, my });
          let chickenBad = false;
          if (hitsChicken(env, impacts)) continue;
          // 直接命中 Bug 会把它炸出地图（第一轮洞 03/09 都因此罚杆 bug-out），一律排除。
          if (bug && impacts.some(im => im.hit === 'object' && im.objId === bug.id)) continue;
          // 推 Bug：推力方向 = 从爆炸点指向 Bug 中心（几何方向，与幅度模型无关）。
          // 必须验证实际落点的推力方向与期望方向一致（现在是二维的）。
          // 直接命中 Bug 会把它炸出地图（bug-out 罚杆），一律排除。
          if ((ap.tag === 'bugPush' || ap.tag === 'bugPushRev') && bug) {
            const bc = objCenter(bug);
            let ok = false;
            for (const im of impacts) {
              if (im.hit === 'object') {
                // 直接打在 Bug 上 → 排除（会炸出地图）
                continue;
              }
              const px = bc.x - im.x, py = bc.y - im.y;
              // 推力方向 (px,py) 必须与期望方向 (ux,uy) 同向
              const dot = px * ap.ux + py * ap.uy;
              if (dot > 4) ok = true;
            }
            if (!ok) continue;
          }
          // 落点必须贴近瞄准点
          let bestD = Infinity, direct = false;
          for (const im of impacts) {
            const d = Math.hypot(im.x - ap.x, im.y - ap.y);
            if (d < bestD) { bestD = d; direct = im.hit === 'object' && im.objId === ap.id; }
          }
          if (bestD > 26 && !direct) continue;
          const outcome = predictOutcome(env, obs, { kind }, impacts, model);
          for (const r of outcome.res) if (r.chicken && (r.dmg > 0 || r.broken)) { chickenBad = true; }
          if (chickenBad || hitsChicken(env, impacts)) continue;
          let sc = scoreOutcome(env, obs, outcome, goals, tokens.length);
          sc += direct ? 80 : -bestD * 1.6;
          if (!st.cur) sc += 2;
          // 防复读：同样参数已经试过且毫无进展 → 大幅降权（但不排除，避免只剩它）
          const stale = staleCount(kind, s.angle, s.power, s.dir);
          if (stale > 0) sc -= 5000 * stale;
          cands.push({
            shot: { kind, angle: Math.round(s.angle * 10) / 10, power: Math.round(s.power * 10) / 10, dir: s.dir },
            score: sc, stand: st,
            aimTag: ap.tag, aimDd: ap.dd, aimUx: ap.ux, aimUy: ap.uy, aimTargetX: ap.targetX,
          });
        }
      }
    }
  }

  if (!cands.length) {
    // 没有任何「贴近瞄准点」的候选：放宽落点要求再找一次，
    // 避免因为筛选过严而白白声明不可打（声明不可打要罚 1 杆）。
    for (const st of stands) {
      const ga = effectiveGa(env, st.x);
      const { mx, my } = muzzleAt(env, st.x, st.feetY, ga);
      for (const ap of aims) {
        const dir = ap.x >= mx ? 1 : -1;
        const sols = solveToPoint(mx, my, ap.x, ap.y, dir, aw, ga, { step: 2 });
        // 兜底也要把高抛解一起试：平弹撞墙时高抛往往才是正解。
        // 挑「落点离瞄准点最近」的那一发，而不是拿第一发（否则必然复读撞墙）。
        let bestC = null;
        const idxs = new Set();
        const M = Math.min(sols.length, 8);
        for (let k = 0; k < M; k++) idxs.add(Math.round(k * (sols.length - 1) / Math.max(1, M - 1)));
        for (const i of [sols.length - 1, sols.length - 2]) if (i >= 0) idxs.add(i);
        for (const i of idxs) {
          const s = sols[i];
          if (!s || s.power < 3) continue;
          const impacts = simulateShot(env, { kind: 'single', angle: s.angle, power: s.power, dir: s.dir, mx, my });
          if (hitsChicken(env, impacts)) continue;
          if (bug && impacts.some(im => im.hit === 'object' && im.objId === bug.id)) continue;
          let d = Infinity;
          for (const im of impacts) d = Math.min(d, Math.hypot(im.x - ap.x, im.y - ap.y));
          if (!bestC || d < bestC.d) {
            bestC = { d, shot: { kind: 'single', angle: Math.round(s.angle * 10) / 10, power: Math.round(s.power * 10) / 10, dir: s.dir } };
          }
        }
        if (bestC) {
          cands.push({
            shot: bestC.shot,
            score: -20 - bestC.d * 0.5, stand: st,
            aimTag: ap.tag, aimDd: ap.dd, aimUx: ap.ux, aimUy: ap.uy, aimTargetX: ap.targetX,
          });
        }
      }
      if (cands.length) break;
    }
  }
  if (!cands.length) {
    // 真的没有任何可行动作时才声明不可打（罚 1 杆并把 Bug 复位）。
    // 注意：只有在连「打在别处、碰不到鸡」的保守一杆都找不到时，才用不可打 ——
    // 因为不可打会额外罚 1 杆并重置 Bug，代价高于打偏一杆。
    const t = tokens[0] || bug;
    if (!t) return { unplayable: true };
    const st = stands[0];
    const dir = t.x >= st.x ? 1 : -1;
    const { mx, my } = muzzleAt(env, st.x, st.feetY, 0);
    const sols = solveToPoint(mx, my, t.x, t.y, dir, aw, 0, { step: 2 });
    // 仍然要避开尖叫鸡：命中鸡 = 罚 1 杆 + 鸡退场，比打不中更糟
    for (const s of sols) {
      if (s.power < 3) continue;
      const impacts = simulateShot(env, { kind: 'single', angle: s.angle, power: s.power, dir: s.dir, mx, my });
      if (hitsChicken(env, impacts)) continue;
      return { kind: 'single', angle: Math.round(s.angle * 10) / 10, power: Math.round(s.power * 10) / 10, dir };
    }
    // 打不到目标又不能碰鸡：找一杆落点远离鸡的（哪怕打不中目标）
    for (const s of sols) {
      if (s.power < 3) continue;
      const impacts = simulateShot(env, { kind: 'single', angle: s.angle, power: s.power, dir: s.dir, mx, my });
      if (hitsChicken(env, impacts)) continue;
      return { kind: 'single', angle: Math.round(s.angle * 10) / 10, power: Math.round(s.power * 10) / 10, dir };
    }
    return { unplayable: true };
  }
  cands.sort((a, b) => b.score - a.score);
  const b = cands[0];
  const out = { kind: b.shot.kind, angle: b.shot.angle, power: b.shot.power, dir: b.shot.dir };
  if (!b.stand.cur) out.moveX = b.stand.x;
  if (b.aimTag === 'bugPush' || b.aimTag === 'bugPushRev') {
    out.bugDd = b.aimDd; out.bugUx = b.aimUx; out.bugUy = b.aimUy; out.bugTargetX = b.aimTargetX;
  }
  return out;
}
