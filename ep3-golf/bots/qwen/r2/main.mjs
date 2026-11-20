// 弹弹高尔夫 Bot：物理瞄准 + 目标策略 + 跨洞记忆
import { createInterface } from 'node:readline';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { simulate } from './physics.mjs';

const CELL = 4, GW = 400, GH = 225;
const DBG = (...a) => process.stderr.write(a.join(' ') + '\n');

// ---------- 地形 ----------
function decodeTerrain(rle) {
  const g = new Uint8Array(GW * GH);
  let i = 0;
  for (const [v, n] of rle) { g.fill(v, i, i + n); i += n; }
  return g;
}
function makeSolid(g) {
  return (x, y) => {
    if (x < 0 || x >= 1600 || y < 0 || y >= 900) return false;
    return g[((y / CELL) | 0) * GW + ((x / CELL) | 0)] === 1;
  };
}
function surfaceY(g, x, minY = 0) {
  if (x < 0 || x >= 1600) return null;
  const cx = (x / CELL) | 0;
  for (let cy = Math.max(0, (minY / CELL) | 0); cy < GH; cy++)
    if (g[cy * GW + cx] === 1) return cy * CELL;
  return null;
}
function losClear(solid, x1, y1, x2, y2) {
  const d = Math.hypot(x2 - x1, y2 - y1), n = Math.max(1, Math.ceil(d / 3));
  for (let i = 1; i < n; i++) {
    const t = i / n;
    if (solid(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)) return false;
  }
  return true;
}

// ---------- 物体几何 ----------
function objCenter(o) {
  if (o.type === 'bug') return { x: o.x, y: o.y - (o.radius || 14) };
  return { x: o.x, y: o.y - (o.height || 28) / 2 };
}
function objShape(o) {
  if (o.type === 'bug') return { id: o.id, x: o.x, y: o.y, r: o.radius || 14 };
  return { id: o.id, x: o.x, y: o.y, w: o.width || 28, h: o.height || 28 };
}
function activeObjects(obs) {
  return obs.objects.filter(o => !o.removed && !o.broken && !o.inRecycle);
}
function isToken(o) { return o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken'; }
function tokenHp(tk) { return tk.hp ?? (tk.type === 'token' ? 1 : tk.type === 'bigToken' ? 240 : 150); }

// ---------- 记忆 ----------
const MEM_DIR = 'bot/memory';
function loadMem() {
  try {
    const m = JSON.parse(readFileSync(MEM_DIR + '/learn.json', 'utf8'));
    if (!m.push || m.push.RP > 160) m.push = { P0: 550, RP: 65, samples: m.push?.samples || [] };
    return m;
  }
  catch { return { bias: { dx: 0, dy: 0, n: 0 }, push: { P0: 550, RP: 65, samples: [] } }; }
}
function saveMem(m) {
  try { mkdirSync(MEM_DIR, { recursive: true }); writeFileSync(MEM_DIR + '/learn.json', JSON.stringify(m)); } catch {}
}

let cfg = null, mem = loadMem(), holeMem = null;
function bodyOf(c) { return c?.BODY || { footOffset: 12, muzzleHeight: 35 }; }
function muzzleFrom(feetX, feetY, groundAngleDeg, c) {
  const b = bodyOf(c), a = groundAngleDeg * Math.PI / 180;
  return { x: feetX - b.muzzleHeight * Math.sin(a), y: feetY - b.muzzleHeight * Math.cos(a) };
}

// 站位候选
function standCandidates(grid, hole, actor, maxN = 10) {
  const cur = { x: actor.x, feetY: actor.y + bodyOf(cfg).footOffset, angle: actor.groundAngle || 0 };
  if (!hole.teeActive) return [cur];
  const tee = hole.tee, minFeet = tee.minFeet ?? 0;
  const out = [];
  const x0 = tee.x0 + 14, x1 = tee.x1 - 14;
  const step = Math.max(6, (x1 - x0) / maxN);
  const solid = makeSolid(grid);
  for (let x = x0; x <= x1 + 0.01; x += step) {
    const fy = surfaceY(grid, x, minFeet);
    if (fy == null || !solid(x, fy + 2)) continue;
    // minFeet 语义保守处理：按 y 下限为主，低值时同时满足"高度下限"解释，避免非法站位
    if (minFeet > 0 && minFeet <= 450 && fy > 900 - minFeet) continue;
    const yl = surfaceY(grid, x - 4, minFeet), yr = surfaceY(grid, x + 4, minFeet);
    let ang = 0;
    if (yl != null && yr != null) ang = Math.atan2(yl - yr, 8) * 180 / Math.PI;
    if (Math.abs(ang) > 44) continue;
    out.push({ x, feetY: fy, angle: ang });
  }
  // 引擎站位倾角与本地台阶估计不同：当前位置必须用引擎真实状态
  for (let i = 0; i < out.length; i++) if (Math.abs(out[i].x - cur.x) < 4) out[i] = cur;
  if (!out.some(p => Math.abs(p.x - cur.x) < 4)) out.unshift(cur);
  return out;
}

// ---------- 伤害 / 危险 ----------
const BASE_DMG = { single: 120, burst: 65, spread: 55, dig: 30 };
function estimateDamage(impacts, obj, kind, solid) {
  const base = BASE_DMG[kind], n = kind === 'burst' ? 3 : 1;
  const c = objCenter(obj);
  let total = 0;
  for (const im of impacts) {
    if (im.hit === 'out') continue;
    let d;
    if (im.objectId === obj.id) d = 0;
    else {
      d = Math.hypot(im.x - c.x, im.y - c.y);
      if (obj.type !== 'bug') d = Math.max(0, d - Math.max(obj.width || 28, obj.height || 28) / 2);
      if (d >= 30) continue;
      if (!losClear(solid, im.x, im.y, c.x, c.y)) continue;
    }
    total += base * (1 - d / 30);
  }
  return total * n;
}
function chickenDanger(impacts, chickens, solid, kind) {
  const craterR = { single: 32, burst: 26, spread: 24, dig: 60 }[kind] || 32;
  for (const im of impacts) {
    if (im.hit === 'out') continue;
    for (const ch of chickens) {
      if (im.objectId === ch.id) return 1e9;
      const c = objCenter(ch);
      if (Math.hypot(im.x - c.x, im.y - c.y) < 44 && losClear(solid, im.x, im.y, c.x, c.y)) return 1e9;
      // 坑可能塌掉鸡脚下的地形
      if (Math.abs(im.x - ch.x) < craterR + 14 && im.y > ch.y - 45) return 1e9;
    }
  }
  return 0;
}

// ---------- 通用瞄准搜索 ----------
// opts: {obj?, kinds, deadline, pushObj?, target:{x,y}}
function searchAttack(obs, grid, target, opts) {
  const solid = makeSolid(grid);
  const shapes = activeObjects(obs).map(objShape);
  const chickens = activeObjects(obs).filter(o => o.type === 'chicken');
  const wind = obs.hole.wind || 0;
  const stands = standCandidates(grid, obs.hole, obs.actor, 9);
  const bias = mem.bias.n >= 4 ? { dx: mem.bias.dx * 0.5, dy: mem.bias.dy * 0.5 } : { dx: 0, dy: 0 };
  const tx = target.x - bias.dx, ty = target.y - bias.dy;
  const deadline = opts.deadline;
  let best = null;
  const evalShot = (st, dir, angle, power, kind) => {
    const mz = muzzleFrom(st.x, st.feetY, st.angle, cfg);
    const launch = angle + dir * st.angle;
    const offs = kind === 'spread' ? [-6, 0, 6] : [0];
    const impacts = offs.map(off => simulate({ x0: mz.x, y0: mz.y, angle: launch, power, dir, wind, solid, objects: shapes, offset: off }));
    return { impacts, st, dir, angle, power, kind };
  };
  const score = (cand) => {
    const im0 = cand.impacts[0];
    if (im0.hit === 'out') return -1e8;
    let s = -Math.hypot(im0.x - tx, im0.y - ty) * 2;
    let dmg = 0;
    if (opts.obj) {
      dmg = estimateDamage(cand.impacts, opts.obj, cand.kind, solid);
      s += dmg * 12;
      if (dmg <= 0) s -= 800;
    }
    s -= chickenDanger(cand.impacts, chickens, solid, cand.kind);
    if (Math.abs(cand.st.x - obs.actor.x) < 3) s += 40; // 原位用引擎倾角，最可靠；少冒站位风险
    if (opts.avoidBands) for (const b of opts.avoidBands) if (Math.abs(cand.angle - b) <= 5) s -= 2000;
    if (opts.pushObj && dmg < tokenHp(opts.pushObj)) {
      const p = opts.pushObj, pc = objCenter(p);
      const pushDir = Math.sign(pc.x - im0.x) || 1;
      const ahead = surfaceY(grid, pc.x + pushDir * 60);
      const here = surfaceY(grid, pc.x);
      if (ahead == null || (here != null && ahead - here > 120)) s -= 4000;
    }
    cand.dmg = dmg;
    return s;
  };
  outer:
  for (const st of stands) {
    for (const kind of opts.kinds) {
      for (const dir of [1, -1]) {
        for (let angle = 4; angle <= 85; angle += 5) {
          for (let power = 12; power <= 100; power += 8) {
            const cand = evalShot(st, dir, angle, power, kind);
            const s = score(cand);
            if (!best || s > best.s) best = { ...cand, s };
          }
          if (Date.now() > deadline) break outer;
        }
      }
    }
  }
  if (best && Date.now() < deadline) {
    const b = best;
    for (let angle = Math.max(0, b.angle - 5); angle <= Math.min(85, b.angle + 5); angle += 1) {
      for (let power = Math.max(4, b.power - 8); power <= Math.min(100, b.power + 8); power += 1.5) {
        const cand = evalShot(b.st, b.dir, angle, power, b.kind);
        const s = score(cand);
        if (s > best.s) best = { ...cand, s };
      }
      if (Date.now() > deadline) break;
    }
  }
  return best;
}

// ---------- Bug 策略 ----------
function bugPlan(obs, grid) {
  const bug = obs.objects.find(o => o.type === 'bug' && !o.removed && !o.inRecycle);
  if (!bug) return null;
  const rec = obs.hole.recycle;
  const bc = objCenter(bug);
  // 瞄回收区近端 35% 处，降低过冲风险（推力实测噪声约±50%）
  const rw = rec.x1 - rec.x0;
  const tx = bc.x < (rec.x0 + rec.x1) / 2 ? rec.x0 + rw * 0.35 : rec.x1 - rw * 0.35;
  const target = { x: tx, y: (rec.y0 + rec.y1) / 2 };
  const need = { x: target.x - bc.x, y: target.y - bc.y };
  const dist = Math.hypot(need.x, need.y);
  if (dist < 1) return null;
  const dir = { x: need.x / dist, y: need.y / dist };
  // 回收区在 Bug 下方且水平覆盖：挖掉支撑让 Bug 掉落，比推更可靠
  const drop = dir.y > 0.45 && bc.x > rec.x0 - 25 && bc.x < rec.x1 + 25;
  return { bug, bc, target, dist, dir, drop };
}
function expectedPush(d) { const m = mem.push; return m.P0 * Math.max(0, 1 - d / m.RP); }

function searchBugShot(obs, grid, plan, deadline) {
  const { bc, dist, dir } = plan;
  const solid = makeSolid(grid);
  const shapes = activeObjects(obs).map(objShape);
  const chickens = activeObjects(obs).filter(o => o.type === 'chicken');
  const wind = obs.hole.wind || 0;
  const stands = standCandidates(grid, obs.hole, obs.actor, 8);
  const bias = mem.bias.n >= 4 ? { dx: mem.bias.dx * 0.5, dy: mem.bias.dy * 0.5 } : { dx: 0, dy: 0 };
  let best = null;
  const cands = [];
  for (const d of [14, 20, 28, 38, 50, 60]) {
    cands.push({ x: bc.x - dir.x * d, y: bc.y - dir.y * d, d });
  }
  const scoreIm = (im, st, dir2, angle, power) => {
    if (im.hit === 'out') return;
    if (chickenDanger([im], chickens, solid, 'single') > 0) return;
    const dActual = Math.hypot(im.x - bc.x, im.y - bc.y);
    if (dActual > mem.push.RP) return;
    const expPush = expectedPush(dActual);
    let pdx = bc.x - im.x, pdy = bc.y - im.y;
    const pl = Math.hypot(pdx, pdy) || 1; pdx /= pl; pdy /= pl;
    const align = pdx * dir.x + pdy * dir.y;
    if (align < 0.2) return;
    const endX = bc.x + pdx * expPush, endY = bc.y + pdy * expPush;
    if (endX < 15 || endX > 1585) return;
    let s = -Math.abs(expPush - dist) * 3 + align * 50 - dActual * 0.05;
    if (Math.abs(st.x - obs.actor.x) < 3) s += 40;
    if (expPush > dist + 160) s -= (expPush - dist - 160) * 2;
    if (!best || s > best.s) best = { s, st, dir: dir2, angle, power, kind: 'single', im, dActual };
  };
  outer:
  for (const c of cands) {
    if (c.x < 8 || c.x > 1592 || c.y < 8 || c.y > 892) continue;
    const tx = c.x - bias.dx, ty = c.y - bias.dy;
    for (const st of stands) {
      for (const dir2 of [1, -1]) {
        for (let angle = 6; angle <= 85; angle += 6) {
          for (let power = 12; power <= 100; power += 8) {
            const mz = muzzleFrom(st.x, st.feetY, st.angle, cfg);
            const im = simulate({ x0: mz.x, y0: mz.y, angle: angle + dir2 * st.angle, power, dir: dir2, wind, solid, objects: shapes });
            scoreIm(im, st, dir2, angle, power);
          }
          if (Date.now() > deadline) break outer;
        }
      }
    }
  }
  if (best && Date.now() < deadline) {
    const b = best;
    for (let angle = Math.max(0, b.angle - 6); angle <= Math.min(85, b.angle + 6); angle += 1) {
      for (let power = Math.max(4, b.power - 8); power <= Math.min(100, b.power + 8); power += 1.5) {
        const mz = muzzleFrom(b.st.x, b.st.feetY, b.st.angle, cfg);
        const im = simulate({ x0: mz.x, y0: mz.y, angle: angle + b.dir * b.st.angle, power, dir: b.dir, wind, solid, objects: shapes });
        scoreIm(im, b.st, b.dir, angle, power);
      }
      if (Date.now() > deadline) break;
    }
  }
  return best;
}

// ---------- 学习 ----------
function learnFromResult(obs) {
  const lr = obs.lastResult;
  if (!lr || !holeMem) return;
  if (holeMem.lastPredict && lr.impacts?.length) {
    const im = lr.impacts[0];
    const dx = im.x - holeMem.lastPredict.x, dy = im.y - holeMem.lastPredict.y;
    if (Math.abs(dx) < 60 && Math.abs(dy) < 60) {
      const b = mem.bias;
      b.dx = (b.dx * b.n + dx) / (b.n + 1); b.dy = (b.dy * b.n + dy) / (b.n + 1); b.n++;
    }
  }
  const dmgEvents = (lr.objectChanges || []).filter(c => {
    if (!((c.damage || 0) > 0)) return false;
    const o = obs.objects.find(q => q.id === c.id);
    return o && o.type !== 'chicken';
  });
  const tokenBroken = (lr.objectChanges || []).some(c => {
    const o = obs.objects.find(q => q.id === c.id);
    return o && isToken(o) && (o.broken || o.removed);
  });
  // token 阶段无伤害 → 记录失败角度带，避免复读
  if (holeMem.lastPhase === 'tokens' && holeMem.lastAction) {
    if (dmgEvents.length === 0 && !tokenBroken) {
      holeMem.failedBands = holeMem.failedBands || [];
      holeMem.failedBands.push(holeMem.lastAction.angle);
      if (holeMem.failedBands.length > 6) holeMem.failedBands.shift();
    } else {
      holeMem.failedBands = [];
      holeMem.lastDig = null;
    }
  }
  for (const c of lr.objectChanges || []) {
    const o = obs.objects.find(q => q.id === c.id);
    if (o?.type === 'bug' && c.before && c.after && holeMem.lastBugShot && !holeMem.lastBugShot.fallback) {
      const moved = Math.hypot(c.after.x - c.before.x, c.after.y - c.before.y);
      const d = holeMem.lastBugShot.d;
      if (d > 5) {
        const ss = mem.push.samples;
        ss.push({ d, moved });
        if (ss.length > 30) ss.shift();
        let bp = { P0: mem.push.P0, RP: mem.push.RP }, be = 1e18;
        for (let P0 = 300; P0 <= 750; P0 += 10) for (let RP = 40; RP <= 160; RP += 2) {
          let e = 2.0 * ((P0 - 550) / 80) ** 2 + 2.0 * ((RP - 65) / 25) ** 2;
          for (const s of ss) e += (P0 * Math.max(0, 1 - s.d / RP) - s.moved) ** 2 / 100;
          if (e < be) { be = e; bp = { P0, RP }; }
        }
        mem.push.P0 = bp.P0; mem.push.RP = bp.RP;
      }
    }
  }
  saveMem(mem);
}

// ---------- 纸飞机（保守：火炮完全卡死时才用） ----------
function tryFly(obs, grid, targetC) {
  if (!(obs.fly > 0) || !obs.hole.teeActive) return null;
  const solid = makeSolid(grid);
  const wind = obs.hole.wind || 0;
  const actor = obs.actor;
  const feetY = actor.y + bodyOf(cfg).footOffset;
  const mz = muzzleFrom(actor.x, feetY, actor.groundAngle || 0, cfg);
  const dir = targetC.x >= actor.x ? 1 : -1;
  const desiredX = Math.max(actor.x + 150, Math.min(targetC.x - 160, 1450));
  if (dir * (desiredX - actor.x) < 100) return null;
  let best = null;
  for (let angle = 15; angle <= 85; angle += 3) {
    for (let power = 20; power <= 100; power += 4) {
      // 飞机不撞物体，只落 terrain；角相对水平，不叠倾角
      const r = simulate({ x0: mz.x, y0: mz.y, angle, power, dir, wind, solid, objects: [] });
      if (r.hit !== 'ground') continue;
      if (dir * (r.x - actor.x) < 100) continue;
      const yl = surfaceY(grid, r.x - 4), yr = surfaceY(grid, r.x + 4);
      if (yl == null || yr == null) continue;
      if (Math.abs(Math.atan2(yl - yr, 8)) > 0.7) continue;
      const s = -Math.abs(r.x - desiredX);
      if (!best || s > best.s) best = { s, angle, power, dir, land: r };
    }
  }
  if (!best) return null;
  return { fly: { angle: best.angle, power: best.power, dir: best.dir } };
}

// ---------- 决策 ----------
function finalize(res, obs) {
  const attack = { kind: res.kind, angle: +res.angle.toFixed(1), power: +res.power.toFixed(1), dir: res.dir };
  const out = { attack };
  if (obs.hole.teeActive && res.st && Math.abs(res.st.x - obs.actor.x) > 2) out.moveX = Math.round(res.st.x);
  return out;
}

function hitsChicken(impacts, obs) {
  const ids = new Set(obs.objects.filter(o => o.type === 'chicken' && !o.removed && !o.broken).map(o => o.id));
  return impacts.some(im => im.objectId != null && ids.has(im.objectId));
}
function safeFinalize(res, obs) {
  if (!res) return null;
  const impacts = res.impacts || (res.im ? [res.im] : []);
  if (hitsChicken(impacts, obs)) return null;
  return finalize(res, obs);
}

function decideTurn(obs) {
  const t0 = Date.now();
  const grid = decodeTerrain(obs.terrain.rle);
  const solid = makeSolid(grid);
  const goal = obs.hole.goal;
  const alive = activeObjects(obs);
  const tokens = alive.filter(isToken);
  const bug = alive.find(o => o.type === 'bug');
  const deadline = t0 + 820;
  const wantTokens = (goal === 'tokens' || goal === 'both') && tokens.length > 0;
  const wantBug = (goal === 'bug' || goal === 'both') && bug && !bug.inRecycle;
  const bands = holeMem.failedBands || [];

  if (!wantTokens && wantBug && holeMem) {
    const bi = holeMem.bugInit;
    const movedFromInit = bi && Math.hypot(bug.x - bi.x, bug.y - bi.y) > 30;
    if (holeMem.noProgress >= 2 && movedFromInit) return { unplayable: true };
  }

  if (wantTokens) {
    holeMem.lastPhase = 'tokens';
    let best = null, bestToken = null, bestScore = -1e18;
    const per = Math.max(150, Math.floor(700 / tokens.length));
    for (const tk of tokens) {
      const hp = tokenHp(tk);
      const kinds = hp > 120 ? ['burst'] : ['single'];
      const res = searchAttack(obs, grid, objCenter(tk), {
        obj: tk, kinds, deadline: Math.min(deadline, Date.now() + per),
        pushObj: tk.type === 'pushToken' ? tk : null, avoidBands: bands,
      });
      if (!res) continue;
      let s = res.s + (res.dmg >= hp ? 6000 : 0);
      if (s > bestScore) { bestScore = s; best = res; bestToken = tk; }
      if (Date.now() > deadline) break;
    }
    if (best && best.dmg > 0) {
      const f = safeFinalize(best, obs);
      if (f) { holeMem.lastPredict = best.impacts[0]; holeMem.lastBugShot = null; holeMem.lastAction = { angle: best.angle, kind: best.kind }; return f; }
    }
    // 无直接伤害：尝试其他弹种 / dig 开路
    const tk = bestToken || tokens[0];
    const alt = searchAttack(obs, grid, objCenter(tk), { obj: tk, kinds: ['spread', 'dig', 'burst', 'single'], deadline, avoidBands: bands });
    if (alt && alt.dmg > 0) {
      const f = safeFinalize(alt, obs);
      if (f) { holeMem.lastPredict = alt.impacts[0]; holeMem.lastBugShot = null; holeMem.lastAction = { angle: alt.angle, kind: alt.kind }; return f; }
    }
    const c = objCenter(tk);
    // dig 渐进：朝上次落点与目标的中点挖，逐步开通道
    let digTarget = c;
    if (holeMem.lastDig && holeMem.noProgress >= 1) {
      digTarget = { x: (holeMem.lastDig.x + c.x) / 2, y: (holeMem.lastDig.y + c.y) / 2 };
    }
    const dig = searchAttack(obs, grid, digTarget, { kinds: ['dig'], deadline, avoidBands: bands });
    const digClose = dig && dig.impacts[0].hit !== 'out' && Math.hypot(dig.impacts[0].x - c.x, dig.impacts[0].y - c.y) < 130;
    if (!digClose || holeMem.noProgress >= 2) {
      const fl = tryFly(obs, grid, c);
      if (fl) return fl;
    }
    if (dig) {
      const f = safeFinalize(dig, obs);
      if (f) { holeMem.lastPredict = dig.impacts[0]; holeMem.lastBugShot = null; holeMem.lastDig = dig.impacts[0]; holeMem.lastAction = { angle: dig.angle, kind: 'dig' }; return f; }
    }
  }

  if (wantBug) {
    holeMem.lastPhase = 'bug';
    const plan = bugPlan(obs, grid);
    if (plan) {
      // 掉落策略：回收区在下方时挖 Bug 脚下支撑
      if (plan.drop) {
        const dt = { x: plan.bug.x, y: plan.bug.y + 4 };
        const dr = searchAttack(obs, grid, dt, { kinds: ['dig', 'single'], deadline: Math.min(deadline, Date.now() + 350), avoidBands: bands });
        if (dr && dr.impacts[0].hit !== 'out' && Math.hypot(dr.impacts[0].x - dt.x, dr.impacts[0].y - dt.y) < 34) {
          const f = safeFinalize(dr, obs);
          if (f) { holeMem.lastPredict = dr.impacts[0]; holeMem.lastBugShot = null; holeMem.lastAction = { angle: dr.angle, kind: dr.kind }; return f; }
        }
      }
      const res = searchBugShot(obs, grid, plan, deadline);
      if (res) {
        const f = safeFinalize(res, obs);
        if (f) { holeMem.lastPredict = res.im; holeMem.lastBugShot = { d: res.dActual }; holeMem.lastAction = { angle: res.angle, kind: 'single' }; return f; }
      }
      const dig = searchAttack(obs, grid, { x: plan.bc.x - plan.dir.x * 45, y: plan.bc.y - plan.dir.y * 45 }, { kinds: ['dig', 'single'], deadline, avoidBands: bands });
      const digClose = dig && dig.impacts[0].hit !== 'out' && Math.hypot(dig.impacts[0].x - plan.bc.x, dig.impacts[0].y - plan.bc.y) < 160;
      if (!digClose || holeMem.noProgress >= 2) {
        const fl = tryFly(obs, grid, plan.bc);
        if (fl) return fl;
      }
      if (dig) {
        const f = safeFinalize(dig, obs);
        if (f) { holeMem.lastPredict = dig.impacts[0]; holeMem.lastBugShot = { d: 45, fallback: true }; holeMem.lastAction = { angle: dig.angle, kind: dig.kind }; return f; }
      }
      if (holeMem.noProgress >= 2) return { unplayable: true };
    }
  }

  const tgt = alive.find(o => o.type !== 'chicken') || alive[0];
  if (tgt) {
    const res = searchAttack(obs, grid, objCenter(tgt), { kinds: ['single'], deadline, avoidBands: bands });
    const f = safeFinalize(res, obs);
    if (f) { holeMem.lastPredict = res.impacts[0]; holeMem.lastAction = { angle: res.angle, kind: 'single' }; return f; }
  }
  // 保底：原位高抛近落，避免鸡击
  const safeDir = obs.actor.x < 800 ? 1 : -1;
  return { attack: { kind: 'single', angle: 85, power: 25, dir: safeDir } };
}

// ---------- 协议 ----------
function respond(r) {
  switch (r.type) {
    case 'init': {
      cfg = r.config;
      holeMem = { bugInit: null, noProgress: 0, lastPredict: null, lastBugShot: null, lastObjSig: null, failedBands: [], lastDig: null, lastPhase: null, lastAction: null };
      const bug = r.objects?.find(o => o.type === 'bug');
      if (bug) holeMem.bugInit = { x: bug.x, y: bug.y };
      DBG(`init hole=${r.hole?.id} goal=${r.hole?.goal} wind=${r.hole?.wind}`);
      return { ok: true };
    }
    case 'turn': {
      try {
        if (!holeMem) holeMem = { bugInit: null, noProgress: 0 };
        learnFromResult(r);
        const sig = JSON.stringify(r.objects.map(o => [o.id, o.broken, o.removed, o.inRecycle, Math.round(o.x / 8), Math.round(o.hp ?? 0)]));
        if (holeMem.lastObjSig === sig) holeMem.noProgress++; else holeMem.noProgress = 0;
        holeMem.lastObjSig = sig;
        const action = decideTurn(r);
        DBG(`turn strokes=${r.strokes} action=${JSON.stringify(action)}`);
        return action;
      } catch (e) {
        DBG('turn error: ' + e.stack);
        return { attack: { kind: 'single', angle: 45, power: 60, dir: 1 } };
      }
    }
    case 'exam': {
      cfg = r.config;
      const preds = r.questions.map(q => {
        const ground = (x, y) => y >= r.ground;
        const res = simulate({ x0: q.x, y0: r.ground - bodyOf(cfg).muzzleHeight, angle: q.angle, power: q.power, dir: q.dir, wind: q.wind, solid: ground });
        return { id: q.id, x: +res.x.toFixed(1), y: +res.y.toFixed(1) };
      });
      return { predictions: preds };
    }
    default:
      return { ok: true };
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  let r;
  try { r = JSON.parse(line); } catch { continue; }
  const reply = respond(r);
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}