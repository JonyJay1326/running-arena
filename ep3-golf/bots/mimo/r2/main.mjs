// 弹弹高尔夫 Bot v3 — 防复读 + 高台适应 + dig开路
import { createInterface } from 'node:readline';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));
const MEM_DIR = join(__dir, 'memory');

// ── 物理常量（试射标定） ──
const GAMMA = 8.33;
const BETA  = 0.312;
const A_COEF = 0.156;
const B_COEF = 4.165;
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
function simTraj(x0, y0, angleDeg, power, dir, wind, terrain, objects, groundAngle) {
  // 实际发射角 = angle + dir × groundAngle
  const actualAngle = (angleDeg + dir * (groundAngle || 0)) * Math.PI / 180;
  const vx = dir * power * Math.cos(actualAngle);
  const vy = -power * Math.sin(actualAngle);
  const aw = A_COEF * wind, g = B_COEF;
  for (let i = 0; i < 6000; i++) {
    const t = i * 0.02;
    const x = x0 + vx * t + aw * t * t;
    const y = y0 + vy * t + g * t * t;
    if (x < -50 || x > 1650 || y > 950 || y < -50) return { hit: { x, y, type: 'out' }, t };
    if (terrain && terrain.isSolid(x, y)) return { hit: { x, y, type: 'ground' }, t };
    if (objects) {
      for (const o of objects) {
        if (o.removed || o.broken || o.stunned) continue;
        if (o.type === 'bug') {
          const dx = x - o.x, dy = y - o.y;
          if (dx * dx + dy * dy <= (o.radius || 14) ** 2) return { hit: { x, y, type: 'object', id: o.id }, t };
        } else {
          const hw = (o.width || 28) / 2;
          if (Math.abs(x - o.x) <= hw && y >= o.y - (o.height || 28) - 2 && y <= o.y + 2)
            return { hit: { x, y, type: 'object', id: o.id }, t };
        }
      }
    }
  }
  return { hit: null, t: 0 };
}

// ── 网格搜索最优射击 ──
function findBestShot(x0, y0, aimX, aimY, wind, preferDir, terrain, objects, avoidIds, targetId, state, groundAngle) {
  let best = null;
  const dirs = preferDir ? [preferDir] : [1, -1];
  const lastImpact = state && state.lastImpact;
  for (const dir of dirs) {
    for (let angle = 2; angle <= 84; angle += 2) {
      for (let power = 10; power <= 100; power += 5) {
        const sim = simTraj(x0, y0, angle, power, dir, wind, terrain, objects, groundAngle);
        if (!sim.hit || sim.hit.type === 'out') continue;
        const impact = sim.hit;
        const dist = Math.hypot(impact.x - aimX, impact.y - aimY);
        let score = -dist;
        if (impact.type === 'object' && impact.id != null) {
          if (avoidIds.includes(impact.id)) score -= 500;
          else if (targetId != null && impact.id === targetId) score += 80;
          else score += 10;
        }
        if (objects) {
          for (const o of objects) {
            if (o.type === 'chicken' && !o.removed && !o.stunned) {
              const cd = Math.hypot(impact.x - o.x, impact.y - (o.y - 15));
              if (cd < 50) score -= 500;
            }
          }
        }
        if (lastImpact && Math.hypot(impact.x - lastImpact.x, impact.y - lastImpact.y) < 30) {
          score -= 150;
        }
        if (!best || score > best.score) best = { angle, power, dir, score, impact, dist };
      }
    }
  }
  return best;
}

// ── 枪口位置 ──
function getMuzzle(actor) {
  const footY = actor.y + FOOT_OFF;
  const ga = (actor.groundAngle || 0) * Math.PI / 180;
  return {
    x: actor.x - MUZZLE_H * Math.sin(ga),
    y: footY - MUZZLE_H * Math.cos(ga)
  };
}

// ── 策略 ──
function chooseAttack(obs, terrain, state) {
  const { hole, actor, objects, lastResult } = obs;
  const wind = hole.wind;
  const goal = hole.goal;

  // 防复读状态更新
  if (lastResult && lastResult.impacts && lastResult.impacts.length > 0) {
    const imp = lastResult.impacts[0];
    state.lastImpact = { x: imp.x, y: imp.y };
    if (!state.stuckCount) state.stuckCount = 0;
    if (imp.objectId == null) state.stuckCount++;
    else state.stuckCount = 0;
  }

  const mz = getMuzzle(actor);
  const active = objects.filter(o => !o.removed && !o.broken && !o.stunned);
  const tokens = active.filter(o => ['token', 'bigToken', 'pushToken'].includes(o.type));
  const bugs = active.filter(o => o.type === 'bug');
  const chickens = active.filter(o => o.type === 'chicken');

  const needTokens = goal === 'tokens' || goal === 'both';
  const needBug = goal === 'bug' || goal === 'both';
  const remainingTokens = tokens.filter(t => !t.broken);
  const bugInRecycle = bugs.length > 0 && bugs.every(b => b.inRecycle);

  let targetObj = null, aimX = 0, aimY = 0, kind = 'single', preferDir = 0;

  if (needTokens && remainingTokens.length > 0) {
    targetObj = remainingTokens.sort((a, b) => Math.abs(a.x - mz.x) - Math.abs(b.x - mz.x))[0];
    aimX = targetObj.x;
    aimY = targetObj.y - (targetObj.height || 28) / 2;
    const hp = targetObj.hp || 1;
    if (hp > 150) kind = 'burst';
    else kind = 'single';
    preferDir = aimX > mz.x ? 1 : -1;
  } else if (needBug && bugs.length > 0 && !bugInRecycle) {
    const bug = bugs[0];
    const recycle = hole.recycle;
    let pushDir = 1;
    if (recycle) {
      const rcx = (recycle.x0 + recycle.x1) / 2;
      pushDir = rcx > bug.x ? 1 : -1;
    }
    // Bug在高台（远高于回收区）→推离边缘让它掉下
    const highBug = recycle && (bug.y < recycle.y0 - 50);
    const offset = highBug ? 35 : 45;
    aimX = bug.x - pushDir * offset;
    aimY = terrain.findSurfaceY(aimX);
    if (Math.abs(aimY - bug.y) > 30) aimY = bug.y;
    kind = 'single';
    preferDir = aimX > mz.x ? 1 : -1;
    targetObj = null;
  } else {
    return { attack: { kind: 'single', angle: 45, power: 50, dir: 1 } };
  }

  const avoidIds = chickens.map(c => c.id);

  // moveX
  let moveX = null;
  if (hole.teeActive && hole.tee) {
    const { x0, x1 } = hole.tee;
    const idealX = Math.max(x0, Math.min(x1, aimX));
    if (Math.abs(idealX - actor.x) > 15) {
      const gy = terrain.findSurfaceY(idealX);
      const minF = hole.tee.minFeet || 0;
      if (gy >= minF && gy < 900) moveX = Math.round(idealX);
    }
  }

  let searchX = mz.x, searchY = mz.y;
  if (moveX != null && moveX !== actor.x) {
    const newFootY = terrain.findSurfaceY(moveX);
    searchX = moveX;
    searchY = newFootY - MUZZLE_H;
  }

  const best = findBestShot(searchX, searchY, aimX, aimY, wind, preferDir, terrain, active,
    avoidIds, targetObj ? targetObj.id : null, state, actor.groundAngle || 0);

  if (best) {
    // 落点远离目标且在地形上→切dig开路
    if (best.dist > 100 && best.impact && best.impact.type === 'ground' && state.stuckCount >= 1) {
      kind = 'dig';
    }
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

  // 兜底
  const dx = aimX - searchX, dy = aimY - searchY;
  return {
    attack: {
      kind,
      angle: Math.round(Math.min(75, Math.max(5, Math.atan2(Math.max(0, -dy), Math.abs(dx)) * 180 / Math.PI))),
      power: Math.round(Math.min(100, Math.max(20, Math.hypot(dx, dy) / 10))),
      dir: dx > 0 ? 1 : -1
    }
  };
}

// ── 高空风修正 ──
function windCorrection(peakH) {
  const h = Math.max(0, peakH - 250);
  return 1 + Math.pow(h, 2.3) / 1763730;
}

// ── 考卷 ──
function handleExam(r) {
  return {
    predictions: r.questions.map(q => {
      const th = q.angle * Math.PI / 180;
      const s = Math.sin(th), c = Math.cos(th);
      const a = B_COEF, b = -q.power * s, cc = -35;
      const disc = b * b - 4 * a * cc;
      if (disc < 0) return { id: q.id, x: q.x, y: 780 };
      const tau = (-b + Math.sqrt(disc)) / (2 * a);
      const peakH = 35 + q.power * q.power * s * s / (4 * B_COEF);
      const dx = q.dir * q.power * c * tau + A_COEF * q.wind * tau * tau * windCorrection(peakH);
      return { id: q.id, x: Math.round((q.x + dx) * 10) / 10, y: 780 };
    })
  };
}

// ── 主循环 ──
let holeState = { stuckCount: 0, lastImpact: null };

function respond(r) {
  switch (r.type) {
    case 'init':
      holeState = { stuckCount: 0, lastImpact: null };
      process.stderr.write(`init: ${r.hole.id} goal=${r.hole.goal} wind=${r.hole.wind}\n`);
      return { ok: true };
    case 'turn': {
      const terrain = new Terrain(r.terrain);
      const action = chooseAttack(r, terrain, holeState);
      process.stderr.write(`turn ${r.strokes}: ${JSON.stringify(action)} stuck=${holeState.stuckCount}\n`);
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
