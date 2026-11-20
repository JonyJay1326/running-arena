// 弹弹高尔夫 Bot v2 — 物理标定 + 策略优化
import { createInterface } from 'node:readline';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));
const MEM_DIR = join(__dir, 'memory');

// ── 物理常量（试射标定） ──
const GAMMA = 8.33;    // g/k²
const BETA  = 0.312;   // β/k²
const A_COEF = 0.156;  // 0.5 * BETA
const B_COEF = 4.165;  // 0.5 * GAMMA
const MUZZLE_H = 35;
const FOOT_OFF = 12;

// ── 地形 ──
class Terrain {
  constructor(td) {
    this.w = td.width; this.h = td.height; this.cell = td.cell;
    this.gw = this.w; this.gh = this.h;
    this.grid = new Uint8Array(this.gw * this.gh);
    let idx = 0;
    for (const [v, c] of td.rle) for (let i = 0; i < c && idx < this.grid.length; i++) this.grid[idx++] = v;
  }
  isSolid(px, py) {
    const cx = Math.floor(px / this.cell), cy = Math.floor(py / this.cell);
    return cx >= 0 && cx < this.gw && cy >= 0 && cy < this.gh && this.grid[cy * this.gw + cx] === 1;
  }
  findSurfaceY(px) {
    const cx = Math.floor(px / this.cell);
    if (cx < 0 || cx >= this.gw) return 780;
    for (let cy = 0; cy < this.gh; cy++) if (this.grid[cy * this.gw + cx] === 1) return cy * this.cell;
    return 780;
  }
}

// ── 轨迹模拟 ──
function simTraj(x0, y0, angleDeg, power, dir, wind, terrain, objects) {
  const th = angleDeg * Math.PI / 180;
  const vx = dir * power * Math.cos(th);
  const vy = -power * Math.sin(th);
  const aw = A_COEF * wind, g = B_COEF;
  const pts = [];
  for (let i = 0; i < 6000; i++) {
    const t = i * 0.02;
    const x = x0 + vx * t + aw * t * t;
    const y = y0 + vy * t + g * t * t;
    pts.push({ x, y });
    if (x < -50 || x > 1650 || y > 950 || y < -50) return { pts, hit: { x, y, type: 'out' }, t };
    if (terrain && terrain.isSolid(x, y)) return { pts, hit: { x, y, type: 'ground' }, t };
    if (objects) {
      for (const o of objects) {
        if (o.removed || o.broken || o.stunned) continue;
        if (o.type === 'bug') {
          const dx = x - o.x, dy = y - o.y;
          if (dx * dx + dy * dy <= (o.radius || 14) ** 2) return { pts, hit: { x, y, type: 'object', id: o.id }, t };
        } else {
          const hw = (o.width || 28) / 2;
          if (Math.abs(x - o.x) <= hw && y >= o.y - (o.height || 28) - 2 && y <= o.y + 2)
            return { pts, hit: { x, y, type: 'object', id: o.id }, t };
        }
      }
    }
  }
  return { pts, hit: null, t: 0 };
}

// ── 解析瞄准 ──
function solveShot(x0, y0, tx, ty, wind, dir) {
  let best = null;
  for (let tau = 0.5; tau < 60; tau += 0.2) {
    const u = dir * (tx - x0 - A_COEF * wind * tau * tau);
    const v = B_COEF * tau * tau - (ty - y0);
    if (u <= 1 || v < -2) continue;
    const pT = Math.sqrt(u * u + v * v);
    const p = pT / tau;
    if (p < 5 || p > 100) continue;
    const ang = Math.atan2(Math.max(0, v), u) * 180 / Math.PI;
    if (ang < 1 || ang > 85) continue;
    const err = Math.abs(p - Math.round(p)) * 0.1 + Math.abs(ang - Math.round(ang)) * 0.05;
    if (!best || err < best.err) best = { angle: Math.round(ang), power: Math.round(p), err };
  }
  return best;
}

// ── 网格搜索最优射击 ──
function findBestShot(x0, y0, aimX, aimY, wind, preferDir, terrain, objects, avoidIds = [], targetId = null) {
  let best = null;
  const dirs = preferDir ? [preferDir] : [1, -1];
  for (const dir of dirs) {
    for (let angle = 3; angle <= 82; angle += 3) {
      for (let power = 15; power <= 100; power += 7) {
        const sim = simTraj(x0, y0, angle, power, dir, wind, terrain, objects);
        if (!sim.hit || sim.hit.type === 'out') continue;
        const impact = sim.hit;
        const dist = Math.hypot(impact.x - aimX, impact.y - aimY);
        let score = -dist;
        if (impact.type === 'object' && impact.id != null) {
          if (avoidIds.includes(impact.id)) score -= 500;
          else if (targetId != null && impact.id === targetId) score += 50;
          else score += 20;
        }
        if (objects) {
          for (const o of objects) {
            if (o.type === 'chicken' && !o.removed && !o.stunned) {
              const cd = Math.hypot(impact.x - o.x, impact.y - (o.y - 15));
              if (cd < 50) score -= 300;
            }
          }
        }
        if (!best || score > best.score) {
          best = { angle, power, dir, score, impact, dist };
        }
      }
    }
  }
  return best;
}

// ── 获取枪口位置 ──
function getMuzzle(actor, config) {
  const footY = actor.y + FOOT_OFF;
  const ga = (actor.groundAngle || 0) * Math.PI / 180;
  return {
    x: actor.x - MUZZLE_H * Math.sin(ga),
    y: footY - MUZZLE_H * Math.cos(ga)
  };
}

// ── 策略 ──
function chooseAttack(obs, terrain, state) {
  const { hole, actor, objects, fly } = obs;
  const wind = hole.wind;
  const goal = hole.goal;
  const cap = hole.cap;
  const par = hole.par;

  const mz = getMuzzle(actor, obs.config);
  const active = objects.filter(o => !o.removed && !o.broken && !o.stunned);
  const tokens = active.filter(o => ['token', 'bigToken', 'pushToken'].includes(o.type));
  const bugs = active.filter(o => o.type === 'bug');
  const chickens = active.filter(o => o.type === 'chicken');

  const needTokens = goal === 'tokens' || goal === 'both';
  const needBug = goal === 'bug' || goal === 'both';
  const remainingTokens = tokens.filter(t => !t.broken);
  const bugInRecycle = bugs.length > 0 && bugs.every(b => b.inRecycle);
  const bugDone = bugs.length === 0 || bugInRecycle;

  // 目标选择
  let targetObj = null, aimX = 0, aimY = 0, kind = 'single', preferDir = 0;

  if (needTokens && remainingTokens.length > 0) {
    // 选最近的 token
    targetObj = remainingTokens.sort((a, b) => Math.abs(a.x - mz.x) - Math.abs(b.x - mz.x))[0];
    // 瞄准 token 中心
    aimX = targetObj.x;
    aimY = targetObj.y - (targetObj.height || 28) / 2;
    // 弹种选择
    const hp = targetObj.hp || 1;
    if (hp > 150) kind = 'burst';       // 大血量用三连发
    else if (hp > 60) kind = 'single';  // 中等血量用单发
    else kind = 'single';
    preferDir = aimX > mz.x ? 1 : -1;
  } else if (needBug && bugs.length > 0 && !bugInRecycle) {
    // 推 Bug：瞄准 Bug 附近的地形（偏移量控制推力）
    const bug = bugs[0];
    const recycle = hole.recycle;
    let pushDir = 1; // 推的方向（bug 移动方向）
    if (recycle) {
      const rcx = (recycle.x0 + recycle.x1) / 2;
      pushDir = rcx > bug.x ? 1 : -1;
    }
    // 爆炸点：bug 背面偏移（让爆炸推 bug 向回收区）
    // 关键：瞄准地形而非 bug 本身，避免 bug 挡住炮弹造成过度推动
    const offset = 45; // 偏移距离
    aimX = bug.x - pushDir * offset;
    // 瞄准 bug 所在高度的地形表面
    aimY = terrain.findSurfaceY(aimX);
    // 如果地形表面太高（嵌入地形），用 bug 高度
    if (Math.abs(aimY - bug.y) > 30) aimY = bug.y;
    kind = 'single';
    preferDir = aimX > mz.x ? 1 : -1;
    targetObj = null; // Bug 推动时不打 bug 本身，打旁边地形
  } else {
    // 没有需要处理的目标
    if (remainingTokens.length > 0) {
      targetObj = remainingTokens[0];
      aimX = targetObj.x;
      aimY = targetObj.y - 14;
      kind = 'single';
      preferDir = aimX > mz.x ? 1 : -1;
    } else if (bugs.length > 0 && !bugInRecycle) {
      const bug = bugs[0];
      aimX = bug.x;
      aimY = bug.y;
      kind = 'single';
      preferDir = aimX > mz.x ? 1 : -1;
      targetObj = bug;
    } else {
      return { attack: { kind: 'single', angle: 45, power: 50, dir: 1 } };
    }
  }

  // 避开鸡
  const avoidIds = chickens.map(c => c.id);

  // moveX 优化：朝目标方向移动（在 tee 范围内）
  let moveX = null;
  if (hole.teeActive && hole.tee) {
    const { x0, x1 } = hole.tee;
    const idealX = Math.max(x0, Math.min(x1, aimX));
    // 只在当前位置偏离较大时移动
    if (Math.abs(idealX - actor.x) > 20) {
      // 检查目标位置能否站稳
      const gy = terrain.findSurfaceY(idealX);
      const minF = hole.tee.minFeet || 0;
      if (gy >= minF && gy < 900) {
        moveX = Math.round(idealX);
      }
    }
  }

  // 如果移动了，更新枪口位置
  let searchX = mz.x, searchY = mz.y;
  if (moveX != null && moveX !== actor.x) {
    const newFootY = terrain.findSurfaceY(moveX);
    searchX = moveX;
    searchY = newFootY - MUZZLE_H;
  }

  // 网格搜索最佳射击
  const best = findBestShot(searchX, searchY, aimX, aimY, wind, preferDir, terrain, active, avoidIds, targetObj ? targetObj.id : null);

  if (best) {
    const action = {
      attack: {
        kind,
        angle: Math.min(85, Math.max(1, best.angle)),
        power: Math.min(100, Math.max(5, best.power)),
        dir: best.dir
      }
    };
    if (moveX != null && Math.abs(moveX - actor.x) > 5) action.moveX = moveX;
    return action;
  }

  // 备用：解析解
  const sol = solveShot(mz.x, mz.y, aimX, aimY, wind, preferDir);
  if (sol) {
    return { attack: { kind, angle: sol.angle, power: sol.power, dir: preferDir || 1 } };
  }

  // 最终兜底
  const dx = aimX - mz.x;
  const dist = Math.abs(dx) || 200;
  return {
    attack: {
      kind,
      angle: Math.round(Math.min(60, Math.max(10, Math.atan2(mz.y - aimY, dist) * 180 / Math.PI))),
      power: Math.round(Math.min(100, Math.max(30, dist / 12))),
      dir: dx > 0 ? 1 : -1
    }
  };
}

// ── 高空风修正（试射标定） ──
function windCorrection(peakHeightAboveGround) {
  const h = Math.max(0, peakHeightAboveGround - 250);
  return 1 + Math.pow(h, 2.3) / 1763730;
}

// ── 考卷 ──
function handleExam(r) {
  return {
    predictions: r.questions.map(q => {
      const th = q.angle * Math.PI / 180;
      const s = Math.sin(th), c = Math.cos(th);
      const dy = 35; // muzzle height
      const a = B_COEF, b = -q.power * s, cc = -dy;
      const disc = b * b - 4 * a * cc;
      if (disc < 0) return { id: q.id, x: q.x, y: 780 };
      const tau = (-b + Math.sqrt(disc)) / (2 * a);
      // 峰值高度（地面以上）
      const peakH = dy + q.power * q.power * s * s / (4 * B_COEF);
      const windCorr = windCorrection(peakH);
      const dx = q.dir * q.power * c * tau + A_COEF * q.wind * tau * tau * windCorr;
      return { id: q.id, x: Math.round((q.x + dx) * 10) / 10, y: 780 };
    })
  };
}

// ── 记忆 ──
function loadMem() {
  try { const p = join(MEM_DIR, 'state.json'); return existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : {}; } catch { return {}; }
}
function saveMem(m) {
  try { mkdirSync(MEM_DIR, { recursive: true }); writeFileSync(join(MEM_DIR, 'state.json'), JSON.stringify(m)); } catch {}
}

const memory = loadMem();

// ── 主循环 ──
function respond(r) {
  switch (r.type) {
    case 'init':
      process.stderr.write(`init: ${r.hole.id} goal=${r.hole.goal} wind=${r.hole.wind}\n`);
      return { ok: true };
    case 'turn': {
      const terrain = new Terrain(r.terrain);
      const action = chooseAttack(r, terrain, {});
      process.stderr.write(`turn ${r.strokes}: ${JSON.stringify(action)}\n`);
      return action;
    }
    case 'exam':
      return handleExam(r);
    default:
      throw new Error(`unknown: ${r.type}`);
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  try {
    const r = JSON.parse(line);
    process.stdout.write(JSON.stringify({ reqId: r.reqId, ...respond(r) }) + '\n');
  } catch (e) {
    process.stderr.write(`err: ${e.message}\n`);
    process.stdout.write(JSON.stringify({ reqId: 0, unplayable: true }) + '\n');
  }
}
