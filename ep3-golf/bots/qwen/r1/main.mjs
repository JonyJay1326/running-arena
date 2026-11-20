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
  if (!out.some(p => Math.abs(p.x - cur.x) < 3)) out.unshift(cur);
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
    if (Math.abs(cand.st.x - obs.actor.x) < 3) s += 5; // 少冒站位验证风险
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
  return { bug, bc, target, dist, dir: { x: need.x / dist, y: need.y / dist } };
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
  for (const c of lr.objectChanges || []) {
    const o = obs.objects.find(q => q.id === c.id);
    if (o?.type === 'bug' && c.before && c.after && holeMem.lastBugShot) {
      const moved = Math.hypot(c.after.x - c.before.x, c.after.y - c.before.y);
      const d = holeMem.lastBugShot.d;
      if (d > 5) {
        const ss = mem.push.samples;
        ss.push({ d, moved });
        if (ss.length > 30) ss.shift();
        let bp = { P0: mem.push.P0, RP: mem.push.RP }, be = 1e18;
        for (let P0 = 300; P0 <= 750; P0 += 10) for (let RP = 40; RP <= 160; RP += 2) {
          let e = 0.6 * ((P0 - 550) / 80) ** 2 + 0.6 * ((RP - 65) / 25) ** 2;
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

function decideTurn(obs) {
  const t0 = Date.now();
  const grid = decodeTerrain(obs.terrain.rle);
  const solid = makeSolid(grid);
  const goal = obs.hole.goal;
  const alive = activeObjects(obs);
  const tokens = alive.filter(isToken);
  const bug = alive.find(o => o.type === 'bug');
  const strokesLeft = obs.hole.cap - obs.strokes;
  const deadline = t0 + 820;
  const wantTokens = (goal === 'tokens' || goal === 'both') && tokens.length > 0;
  const wantBug = (goal === 'bug' || goal === 'both') && bug && !bug.inRecycle;

  if (!wantTokens && wantBug && holeMem) {
    const bi = holeMem.bugInit;
    const movedFromInit = bi && Math.hypot(bug.x - bi.x, bug.y - bi.y) > 30;
    if (holeMem.noProgress >= 2 && movedFromInit) return { unplayable: true };
  }

  if (wantTokens) {
    let best = null, bestToken = null, bestScore = -1e18;
    const per = Math.max(150, Math.floor(700 / tokens.length));
    for (const tk of tokens) {
      const hp = tokenHp(tk);
      const kinds = hp > 120 ? ['burst'] : ['single'];
      const res = searchAttack(obs, grid, objCenter(tk), {
        obj: tk, kinds, deadline: Math.min(deadline, Date.now() + per),
        pushObj: tk.type === 'pushToken' ? tk : null,
      });
      if (!res) continue;
      let s = res.s + (res.dmg >= hp ? 6000 : 0);
      if (s > bestScore) { bestScore = s; best = res; bestToken = tk; }
      if (Date.now() > deadline) break;
    }
    if (best && best.dmg > 0) {
      holeMem.lastPredict = best.impacts[0]; holeMem.lastBugShot = null;
      return finalize(best, obs);
    }
    // 无直接伤害：尝试其他弹种 / dig 开路
    const tk = bestToken || tokens[0];
    const alt = searchAttack(obs, grid, objCenter(tk), { obj: tk, kinds: ['spread', 'dig', 'burst', 'single'], deadline });
    if (alt && alt.dmg > 0) {
      holeMem.lastPredict = alt.impacts[0]; holeMem.lastBugShot = null;
      return finalize(alt, obs);
    }
    const c = objCenter(tk);
    const dig = searchAttack(obs, grid, { x: c.x, y: c.y }, { kinds: ['dig'], deadline });
    const digClose = dig && dig.impacts[0].hit !== 'out' && Math.hypot(dig.impacts[0].x - c.x, dig.impacts[0].y - c.y) < 130;
    if (!digClose) {
      const fl = tryFly(obs, grid, c);
      if (fl) return fl;
    }
    if (dig) { holeMem.lastPredict = dig.impacts[0]; holeMem.lastBugShot = null; return finalize(dig, obs); }
  }

  if (wantBug) {
    const plan = bugPlan(obs, grid);
    if (plan) {
      const res = searchBugShot(obs, grid, plan, deadline);
      if (res) {
        holeMem.lastPredict = res.im;
        holeMem.lastBugShot = { d: res.dActual };
        return finalize(res, obs);
      }
      const dig = searchAttack(obs, grid, { x: plan.bc.x - plan.dir.x * 45, y: plan.bc.y - plan.dir.y * 45 }, { kinds: ['dig', 'single'], deadline });
      const digClose = dig && dig.impacts[0].hit !== 'out' && Math.hypot(dig.impacts[0].x - plan.bc.x, dig.impacts[0].y - plan.bc.y) < 160;
      if (!digClose) {
        const fl = tryFly(obs, grid, plan.bc);
        if (fl) return fl;
      }
      if (dig) { holeMem.lastPredict = dig.impacts[0]; holeMem.lastBugShot = { d: 45 }; return finalize(dig, obs); }
    }
  }

  const tgt = alive.find(o => o.type !== 'chicken') || alive[0];
  if (tgt) {
    const res = searchAttack(obs, grid, objCenter(tgt), { kinds: ['single'], deadline });
    if (res) { holeMem.lastPredict = res.impacts[0]; return finalize(res, obs); }
  }
  return { attack: { kind: 'single', angle: 45, power: 60, dir: 1 } };
}

// ---------- 协议 ----------
function respond(r) {
  switch (r.type) {
    case 'init': {
      cfg = r.config;
      holeMem = { bugInit: null, noProgress: 0, lastPredict: null, lastBugShot: null, lastObjSig: null };
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