// 弹弹高尔夫 Bot
// 物理参数来自试射拟合（见 NOTES.md）：v=8.488*power, g=600(标度), 风=22.56*w*f(y)，
// f(y)=1.36(y<385)/1.05(385≤y<440)/1.0(y≥440)
// 策略：每杆在发球台内选站位，对每个目标网格搜索(角度,力度)使模拟落点最贴近期望爆点，
// 评估四种弹种的期望收益与鸡的安全，选最优；被地形挡住时用掘进弹开路；实在打不到且还有纸飞机就飞。
import { createInterface } from 'node:readline';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { PHYS, Terrain, simulateShot, predictFlat } from './physics.mjs';

const MEM = new URL('./memory/', import.meta.url);
const TURN_BUDGET_MS = 650;

// ---------- 运行时记忆（跨洞校准，按洞 id 分桶 + 全局） ----------
function loadCalib() {
  try { return JSON.parse(readFileSync(new URL('./calib.json', MEM), 'utf8')); } catch { return { global: { n: 0, sdx: 0, sdy: 0 }, holes: {} }; }
}
function saveCalib(c) {
  try { mkdirSync(MEM, { recursive: true }); writeFileSync(new URL('./calib.json', MEM), JSON.stringify(c)); } catch (e) { process.stderr.write('calib写失败:' + e.message + '\n'); }
}
function pickBias(holeId) {
  const h = S.calib.holes?.[holeId];
  if (h && h.n >= 2) return { x: h.sdx / h.n, y: h.sdy / h.n };
  const g = S.calib.global;
  if (g.n >= 4) return { x: g.sdx / g.n, y: g.sdy / g.n };
  return { x: 0, y: 0 };
}

const S = { calib: loadCalib(), lastShot: null, cfg: null };

// ---------- 几何/伤害工具 ----------
function center(o) {
  if (o.type === 'bug') return { x: o.x, y: o.y - (o.radius ?? 14) };
  const h = o.height ?? 28;
  return { x: o.x, y: o.y - h / 2 };
}
// 爆点到物体最近表面的距离
function nearDist(p, o) {
  if (o.type === 'bug') { const c = center(o); return Math.hypot(p.x - c.x, p.y - c.y) - (o.radius ?? 14); }
  const hw = (o.width ?? 28) / 2, h = o.height ?? 28, cy = o.y - h / 2;
  const dx = Math.max(Math.abs(p.x - o.x) - hw, 0);
  const dy = Math.max(Math.abs(p.y - cy) - h / 2, 0);
  return Math.hypot(dx, dy);
}
function los(T, p, q) {
  for (let i = 1; i < 8; i++) {
    if (T.solid(p.x + (q.x - p.x) * i / 8, p.y + (q.y - p.y) * i / 8)) return false;
  }
  return true;
}

// ---------- 站位 ----------
function standFoot(T, x, minFeet) {
  const c = T.cell, cx = Math.floor(x / c);
  if (cx < 0 || cx >= T.w) return null;
  const start = Math.ceil((minFeet ?? 0) / c);
  for (let cy = start; cy < T.h; cy++) {
    if (T.g[cy * T.w + cx]) {
      const foot = cy * c;
      // 身体占位（宽26 高60）三列检查
      for (const bx of [x - 10, x, x + 10]) {
        for (let by = foot - 60; by <= foot - 4; by += 4) {
          if (by < 0 || T.solid(bx, by)) return null;
        }
      }
      // 保守站稳判定：脚下列及左右 12px 列在脚部附近有支撑
      for (const sx of [x - 12, x, x + 12]) {
        if (!T.solid(sx, foot) && !T.solid(sx, foot - c) && !T.solid(sx, foot + c)) return null;
      }
      return foot;
    }
  }
  return null;
}
function standCandidates(obs, T) {
  const out = [{ x: obs.actor.x, mx: obs.actor.x, my: obs.actor.y + 12 - 35, ga: obs.actor.groundAngle ?? 0, cur: true }];
  const tee = obs.hole.tee;
  if (!(obs.hole.teeActive && tee)) return out;
  const minFeet = tee.minFeet;
  const spots = [];
  for (let x = Math.max(tee.x0 + 14, 20); x <= Math.min(tee.x1 - 14, 1580); x += 12) {
    if (Math.abs(x - obs.actor.x) < 10) continue;
    const f = standFoot(T, x, minFeet), fl = standFoot(T, x - 8, minFeet), fr = standFoot(T, x + 8, minFeet);
    if (f == null || fl == null || fr == null) continue;
    if (Math.abs(fr - fl) > 0.5) continue; // 只移到完全平坦处：斜坡 groundAngle 符号未验证，不冒险
    spots.push({ x, mx: x, my: f - 35, ga: 0, cur: false });
  }
  // 最多取 5 个：优先离目标近的由调用方排序，这里均匀抽样
  if (spots.length > 5) {
    const pick = [];
    const step = (spots.length - 1) / 4;
    for (let i = 0; i < 5; i++) pick.push(spots[Math.round(i * step)]);
    return out.concat(pick);
  }
  return out.concat(spots);
}

// ---------- 搜索一发 ----------
// errFn(impact, angle) -> 误差；pen(impact) -> 额外惩罚（如鸡风险）；返回 {stand, dir, angle, power, err, impact}
function searchShot(T, wind, stand, dir, errFn, blockers, deadline, pen) {
  const shoot = (a, p, dt) => simulateShot({
    terrain: T, wind, fromX: stand.mx, fromY: stand.my,
    angleDeg: a + dir * stand.ga, power: p, dir, objs: blockers, maxT: 8, dt, margin: 3,
  });
  let best = null;
  const evalp = (a, p) => {
    const r = shoot(a, p, 1 / 120);
    if (r.out && !r.hitObject) return;
    if (r.hitObject && r.hitObject.type === 'chicken') return; // 直接打鸡的弹道直接放弃
    const err = errFn(r, a) + (pen ? pen(r) : 0);
    if (!best || err < best.err) best = { stand, dir, angle: a, power: p, err, impact: r, coarse: true };
  };
  for (let a = 3; a <= 84; a += 4) {
    for (let p = 10; p <= 100; p += 5) evalp(a, p);
    if (Date.now() > deadline) break;
  }
  if (!best) return null;
  const a0 = Math.max(0, best.angle - 4), a1 = Math.min(85, best.angle + 4);
  const p0 = Math.max(5, best.power - 6), p1 = Math.min(100, best.power + 6);
  for (let a = a0; a <= a1; a++) {
    for (let p = p0; p <= p1; p += 2) {
      if (Date.now() > deadline) break;
      evalp(a, p);
    }
  }
  // 终评用细步长 + 无裕度（用于校准的真实预测）
  const r = shoot(best.angle, best.power, 1 / 480);
  const exact = simulateShot({ terrain: T, wind, fromX: stand.mx, fromY: stand.my, angleDeg: best.angle + dir * stand.ga, power: best.power, dir, objs: blockers, maxT: 8 });
  best.impact = exact.out && !exact.hitObject ? r : exact;
  best.err = errFn(best.impact, best.angle);
  return best;
}

// ---------- 评估某目标的最优射击 ----------
function bestForTarget(obs, T, tgt, stands, blockers, chickens, deadline) {
  const wind = obs.hole.wind;
  // 误差函数：打靶=爆点到物体表面距离；推虫=到期望爆点距离（虫贴近回收区时偏好陡弹道短推）
  const errFn = tgt.type === 'break'
    ? r => nearDist(r, tgt.obj)
    : (r, a) => Math.hypot(r.x - tgt.desired.x, r.y - tgt.desired.y) + (tgt.steepPref && a < 40 ? (40 - a) * 0.25 : 0);
  const results = [];
  const pen = chickens.length
    ? r => { let p = 0; for (const ch of chickens) { const d = nearDist(r, ch); if (d < 34) p += 3000 + (34 - d) * 100; } return p; }
    : null;
  for (const stand of stands) {
    for (const dir of [1, -1]) {
      if (Date.now() > deadline) break;
      const r = searchShot(T, wind, stand, dir, errFn, blockers, deadline, pen);
      if (r) results.push({ ...r, tgt });
    }
  }
  if (!results.length) return null;
  results.sort((a, b) => a.err - b.err);
  // 站位微调精修：沿发球台 ±16px 步进 4，细调落点
  const best = results[0];
  if (obs.hole.teeActive && obs.hole.tee) {
    const tee = obs.hole.tee, minFeet = tee.minFeet;
    for (let dx = -16; dx <= 16; dx += 4) {
      if (Date.now() > deadline) break;
      const nx = best.stand.mx + dx;
      if (nx < tee.x0 + 14 || nx > tee.x1 - 14 || nx < 20 || nx > 1580) continue;
      const f = standFoot(T, nx, minFeet);
      if (f == null) continue;
      const fl = standFoot(T, nx - 8, minFeet), fr = standFoot(T, nx + 8, minFeet);
      if (fl == null || fr == null || Math.abs(fr - fl) > 0.5) continue;
      const r = simulateShot({ terrain: T, wind, fromX: nx, fromY: f - 35, angleDeg: best.angle, power: best.power, dir: best.dir, objs: blockers, maxT: 8, margin: 3 });
      if (r.out && !r.hitObject) continue;
      if (r.hitObject && r.hitObject.type === 'chicken') continue;
      const err = errFn(r, best.angle) + (pen ? pen(r) : 0);
      if (err < best.err - 0.5) {
        best.stand = { x: nx, mx: nx, my: f - 35, ga: 0, cur: Math.abs(nx - obs.actor.x) < 2 };
        best.impact = r; best.err = err;
      }
    }
  }
  return best;
}

// 期望得分：爆点对各物体的伤害收益 - 鸡风险
function evalImpact(obs, T, impact, target, kind, chickens, extraImpacts) {
  const pts = [impact, ...(extraImpacts ?? [])];
  let dmg = 0;
  for (const p of pts) {
    const nd = nearDist(p, target);
    const dd = kind === 'burst' ? 65 : kind === 'spread' ? 55 : kind === 'dig' ? 30 : 120;
    if (nd < 30 && los(T, p, center(target))) dmg += dd * (1 - nd / 30);
  }
  if (kind === 'burst') {
    // 实测：pushToken 挨第一发即被推走，后两发落空(65/195)；bigToken 静止但地形被炸后部分偏(137/195)
    dmg *= 3 * (target.type === 'pushToken' ? 0.35 : target.type === 'bigToken' ? 0.72 : 0.5);
  }
  const hp = target.hp ?? target.maxHp ?? 1;
  let score = 110 * Math.min(dmg, hp) / hp;
  // 鸡安全
  for (const p of pts) {
    for (const c of chickens) {
      if (nearDist(p, c) < 38) score -= 10000;
      if (impact.hitObject && impact.hitObject.id === c.id) score -= 1e6;
    }
  }
  return { score, dmg };
}

// ---------- 主决策 ----------
function decide(obs) {
  const t0 = Date.now();
  const deadline = t0 + TURN_BUDGET_MS;
  // 校准：上杆预测 vs 实际
  try { updateCalib(obs); } catch (e) { process.stderr.write('calib错:' + e.message + '\n'); }
  const T = new Terrain(obs.terrain);
  const alive = obs.objects.filter(o => !o.removed && !o.broken && !o.inRecycle);
  const tokens = alive.filter(o => o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken');
  const bug = alive.find(o => o.type === 'bug');
  const chickens = alive.filter(o => o.type === 'chicken');
  const goal = obs.hole.goal;
  const needTokens = goal === 'tokens' || goal === 'both';
  const needBug = goal === 'bug' || goal === 'both';
  const bias = pickBias(obs.hole.id);
  process.stderr.write(`洞${obs.hole.id} 杆${obs.strokes} 目标${goal} 活体${alive.length} 校准(${bias.x.toFixed(1)},${bias.y.toFixed(1)})n=${S.calib.global.n}\n`);

  const stands = standCandidates(obs, T);
  const blockers = alive.map(o => o.type === 'bug'
    ? { id: o.id, type: 'bug', r: o.radius ?? 14, x: o.x, y: o.y }
    : { id: o.id, type: o.type, w: o.width ?? 28, h: o.height ?? 28, x: o.x, y: o.y });

  // 目标列表
  const tgts = [];
  if (needTokens) {
    for (const o of tokens) {
      const c = center(o);
      tgts.push({ obj: o, type: 'break', desired: { x: c.x - bias.x, y: c.y - bias.y } });
    }
  }
  if (needBug && bug && obs.hole.recycle) {
    const rc = obs.hole.recycle;
    const bc = center(bug);
    // 虫已在回收区内（可能还在滑动未标记）时不要再推，等它停
    const inside = bc.x > rc.x0 + 8 && bc.x < rc.x1 - 8 && bc.y > rc.y0 + 8 && bc.y < rc.y1 - 8;
    if (!inside) {
      const px = Math.max(rc.x0, Math.min(rc.x1, bc.x)), py = Math.max(rc.y0, Math.min(rc.y1, bc.y));
      let ux = px - bc.x, uy = py - bc.y;
      const d = Math.hypot(ux, uy) || 1;
      ux /= d; uy /= d;
      const off = 30; // 爆点在虫背离回收区一侧；实测近距爆炸(距心14px)推力极大(+143px)
      tgts.push({
        obj: bug, type: 'push',
        // 实测平射推虫滑行很远(+358)，虫贴近回收区时偏好陡弹道（短推防飞过）
        steepPref: d < 170,
        desired: { x: bc.x - ux * off - bias.x, y: bc.y - uy * off - bias.y },
      });
    }
  }
  if (!tgts.length) {
    return safeShot(obs, alive);
  }

  // 每目标搜索（按目标数均分时间预算，避免前面的目标吃光预算）
  const cands = [];
  const per = tgts.length ? (TURN_BUDGET_MS - 150) / tgts.length : TURN_BUDGET_MS;
  for (let i = 0; i < tgts.length; i++) {
    const tgtDeadline = Math.min(deadline - 100, t0 + 150 + per * (i + 1));
    if (Date.now() > tgtDeadline - 50) continue;
    const r = bestForTarget(obs, T, tgts[i], stands, blockers, chickens, tgtDeadline);
    if (r) cands.push(r);
  }
  if (!cands.length) {
    return safeShot(obs, alive);
  }
  cands.sort((a, b) => a.err - b.err);
  const best = cands[0];
  process.stderr.write(`最佳: 目标#${best.tgt.obj.id}${best.tgt.type} err=${best.err.toFixed(1)} 站${best.stand.mx.toFixed(0)} a${best.angle} p${best.power} d${best.dir} 落(${best.impact.x.toFixed(0)},${best.impact.y.toFixed(0)})\n`);

  // ---- 选择弹种：推虫固定 single；打靶在最佳参数上比较 ----
  let kindPick = 'single';
  if (best.tgt.type === 'break') {
    const wind = obs.hole.wind;
    let kindScore = -Infinity;
    for (const kind of ['single', 'burst', 'spread', 'dig']) {
      if (Date.now() > deadline + 200) break;
      let extra = [];
      if (kind === 'spread') {
        for (const da of [-6, 6]) {
          const r = simulateShot({ terrain: T, wind, fromX: best.stand.mx, fromY: best.stand.my, angleDeg: best.angle + da + best.dir * best.stand.ga, power: best.power, dir: best.dir, objs: blockers, maxT: 8, margin: 3 });
          if (!r.out || r.hitObject) extra.push(r);
        }
      }
      const ev = evalImpact(obs, T, best.impact, best.tgt.obj, kind, chickens, extra);
      const s = ev.score - (kind === 'dig' ? 15 : 0);
      process.stderr.write(`  ${kind}: 分${s.toFixed(1)} 伤${ev.dmg.toFixed(0)}\n`);
      if (s > kindScore) { kindScore = s; kindPick = kind; }
    }
  }

  // ---- 打不到：开路 / 飞 ----
  const poorErr = best.err > 40;
  if (poorErr) {
    // 若最佳落点是挡路的地形且在站位与目标之间，用 dig 开路
    const lo = Math.min(best.stand.mx, best.tgt.desired.x) - 30, hi = Math.max(best.stand.mx, best.tgt.desired.x) + 30;
    const between = best.impact.x >= lo && best.impact.x <= hi;
    if (best.impact.hit === 'terrain' && between) {
      process.stderr.write(`开路: dig 至(${best.impact.x.toFixed(0)},${best.impact.y.toFixed(0)})\n`);
      return act(best, 'dig', obs);
    }
    if (obs.fly > 0 && best.err > 70) {
      const fl = planFly(obs, T, best.tgt, t0);
      if (fl) return fl;
    }
  }
  return act(best, kindPick, obs);
}

// 无目标可打时的安全兜底：朝活体更少的一侧放一小炮（不 moveX）
function safeShot(obs, alive) {
  let l = 0, r = 0;
  for (const o of alive) {
    if (o.x < obs.actor.x - 60) l++;
    else if (o.x > obs.actor.x + 60) r++;
  }
  const dir = l <= r ? -1 : 1;
  return { attack: { kind: 'single', angle: 45, power: 20, dir } };
}

function act(best, kind, obs) {
  S.lastShot = {
    kind, stand: best.stand, dir: best.dir, angle: best.angle, power: best.power, ga: best.stand.ga,
    pred: { x: best.impact.x, y: best.impact.y }, targetId: best.tgt?.obj?.id,
  };
  const a = { attack: { kind, angle: best.angle, power: best.power, dir: best.dir } };
  if (!best.stand.cur && obs.hole.teeActive) a.moveX = Math.round(best.stand.mx);
  return a;
}

// ---------- 纸飞机 ----------
function planFly(obs, T, tgt, t0) {
  const tc = center(tgt.obj);
  const dir = Math.sign(tc.x - obs.actor.x) || 1;
  // 期望落点：目标前方 140px 的可站稳处
  for (let dx of [140, 220, 80, 300]) {
    const lx = Math.max(30, Math.min(1570, tc.x - dir * dx));
    const foot = standFoot(T, lx, obs.hole.tee?.minFeet);
    if (foot == null) continue;
    const ly = foot;
    let best = null;
    for (let a = 15; a <= 80; a += 5) {
      for (let p = 15; p <= 100; p += 5) {
        if (Date.now() - t0 > 850) return null;
        const r = simulateShot({ terrain: T, wind: obs.hole.wind, fromX: obs.actor.x, fromY: obs.actor.y + 12 - 35, angleDeg: a, power: p, dir, objs: null, maxT: 10 });
        if (r.out) continue;
        const err = Math.hypot(r.x - lx, r.y - ly);
        if (!best || err < best.err) best = { a, p, err };
      }
    }
    if (best && best.err < 40) {
      process.stderr.write(`飞机: a${best.a} p${best.p} 落(${lx},${ly}) err${best.err.toFixed(0)}\n`);
      S.lastShot = null;
      return { fly: { angle: best.a, power: best.p, dir } };
    }
  }
  return null;
}

// ---------- 校准 ----------
function updateCalib(obs) {
  const ls = S.lastShot;
  if (!ls || ls.kind !== 'single') return;
  if (Math.abs(ls.ga || 0) > 3) return; // 斜坡上炮口模型近似，不校准
  const lr = obs.lastResult;
  if (!lr || !lr.impacts || !lr.impacts.length) return;
  const act = lr.impacts[0];
  if (act.objectId != null) return; // 直接命中物体的落点不含纯弹道信息
  const dx = act.x - ls.pred.x, dy = act.y - ls.pred.y;
  if (Math.abs(dx) > 120 || Math.abs(dy) > 120) return; // 异常离群不吸收
  const hid = obs.hole?.id ?? '_';
  for (const bucket of [S.calib.global, (S.calib.holes[hid] ??= { n: 0, sdx: 0, sdy: 0 })]) {
    bucket.n++; bucket.sdx += dx; bucket.sdy += dy;
  }
  saveCalib(S.calib);
  process.stderr.write(`校准: 实际(${act.x.toFixed(0)},${act.y.toFixed(0)}) 预测(${ls.pred.x.toFixed(0)},${ls.pred.y.toFixed(0)}) d(${dx.toFixed(1)},${dy.toFixed(1)})\n`);
}

// ---------- 协议 ----------
function respond(r) {
  if (r.type === 'init') {
    S.cfg = r.config;
    return { ok: true };
  }
  if (r.type === 'exam') {
    const g = r.ground ?? 780;
    return {
      predictions: (r.questions ?? []).map(q => {
        const p = predictFlat(q.wind, q.x, q.angle, q.power, q.dir, g);
        return { id: q.id, x: +p.x.toFixed(1), y: +p.y.toFixed(1) };
      }),
    };
  }
  if (r.type === 'turn') {
    try {
      return decide(r);
    } catch (e) {
      process.stderr.write('决策异常:' + (e && e.stack || e) + '\n');
      return safeShot(r, (r.objects ?? []).filter(o => !o.removed && !o.broken && !o.inRecycle));
    }
  }
  throw new Error('未知消息类型:' + r.type);
}

for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  let r;
  try { r = JSON.parse(line); } catch (e) { process.stderr.write('解析失败:' + line.slice(0, 100) + '\n'); continue; }
  const reply = respond(r);
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
