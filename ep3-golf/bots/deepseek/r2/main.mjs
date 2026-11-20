// 弹弹高尔夫 Bot —— 第一轮参赛版
// 物理模型（经 14 发试射标定）：
//   用 τ = t·sqrt(g) 参数化，消去未知的 g：
//   x(τ) = xm + dir·A·cosθ·τ + 风漂移(τ)
//   y(τ) = ym − A·sinθ·τ + 0.5·τ²
//   其中 A = sqrt(S)，S = v²/g = 0.12·power²（拟合误差 <1px）
//   风只影响水平：d²x/dτ² = K0·wind，K0 = 0.03753；
//   高度超过炮口 300px 以上时风增强到 1.31 倍（85° 高抛实测 1.19~1.23，用台阶近似）。
//   炮口：脚底 = actor.y + 12，炮口 = 脚底沿身体法线上移 35。
import { createInterface } from 'node:readline';
import { readFileSync, writeFileSync, appendFileSync, existsSync, mkdirSync } from 'node:fs';

const CFG = {
  S_COEF: 0.12,       // v²/g = S_COEF * power²
  K0: 0.03753,        // 风在 τ 空间的加速度系数
  WIND_ALT: 300,      // 高于炮口多少像素开始变强
  WIND_BOOST: 1.31,
  MUZZLE_H: 35,
  FOOT_OFF: 12,
  DMG_R: 30,
  DMG: { single: 120, burst: 65, spread: 55, dig: 30 },
  CRATER: { single: 32, burst: 26, spread: 24, dig: 60 },
  SPREAD_OFF: [-6, 0, 6],
  PUSH_MAX: 480,      // 爆炸把 bug 推开的估计距离（像素）
  PUSH_R: 45,         // 推力衰减尺度：实测 12px 偏移推了 543px、30px 偏移推了 173px
  BUG_PUSH_RANGE: 62, // bug 被推的有效范围（比伤害半径 30 大，实测 44px 外仍能推动）
  MEM_DIR: 'bot/memory',
};

function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
function hypot(x, y) { return Math.sqrt(x * x + y * y); }

// ---------------- 地形 / 世界 ----------------
function decodeTerrain(t) {
  const w = t.width, h = t.height, cell = t.cell || 4;
  const grid = new Uint8Array(w * h);
  let idx = 0;
  for (const pair of t.rle) {
    const val = pair[0], cnt = pair[1];
    if (cnt > 0 && idx < grid.length) grid.fill(val ? 1 : 0, idx, Math.min(idx + cnt, grid.length));
    idx += cnt;
  }
  return { w, h, cell, grid };
}

function makeWorld(obs) {
  const terr = decodeTerrain(obs.terrain);
  const objects = (obs.objects || []).map(o => ({ ...o }));
  return {
    terr,
    objects,
    wind: obs.hole && obs.hole.wind != null ? obs.hole.wind : 0,
    actor: obs.actor,
    hole: obs.hole,
    solidAt(x, y) {
      const cx = Math.floor(x / this.terr.cell), cy = Math.floor(y / this.terr.cell);
      // 地图上方（y<0）是空气：炮口/弹道可能高于地图顶，不能当成出界（第一轮洞 04 就是因此全程误判 OOB）
      if (cy < 0) return 0;
      if (cx < 0 || cx >= this.terr.w || cy >= this.terr.h) return 2; // 左右出图或掉到地图下方
      return this.terr.grid[cy * this.terr.w + cx] ? 1 : 0;
    },
    // 返回该点命中的物体（未退场的）
    objAt(x, y) {
      for (const o of this.objects) {
        if (o.removed || o.broken || o.stunned) continue;
        if (o.type === 'bug') {
          if (hypot(x - o.x, y - (o.y - o.radius)) <= o.radius) return o;
        } else {
          const hw = (o.width || 28) / 2, ht = (o.height || 28);
          if (x >= o.x - hw && x <= o.x + hw && y >= o.y - ht && y <= o.y) return o;
        }
      }
      return null;
    },
    height: 0,
  };
}

function muzzlePoint(world) {
  const g = (world.actor.groundAngle || 0) * Math.PI / 180;
  const footY = world.actor.y + CFG.FOOT_OFF;
  return { x: world.actor.x + CFG.MUZZLE_H * Math.sin(g), y: footY - CFG.MUZZLE_H * Math.cos(g) };
}

// ---------------- 弹道模拟 ----------------
// 返回 {x,y,oob,obj} —— 该发炮弹的落点/命中物
function march(world, m, A, th, dir) {
  const vx0 = dir * A * Math.cos(th);
  const vy0 = -A * Math.sin(th);
  const wind = world.wind * CFG.K0;
  let x = m.x, y = m.y, vx = vx0, vy = vy0;
  // 步长要细：真实地形有 8px 厚的薄平台，步长过大（0.3τ≈10px）会直接穿过去，导致落点判定错 50px+
  const dtau = 0.15;
  let px = x, py = y; // 上一个空点
  for (let tau = 0; tau < 400; tau += dtau) {
    const alt = m.y - y;
    const f = alt > CFG.WIND_ALT ? CFG.WIND_BOOST : 1;
    const ax = wind * f;
    const nx = x + vx * dtau + 0.5 * ax * dtau * dtau;
    const ny = y + vy * dtau + 0.5 * dtau * dtau;
    vx += ax * dtau;
    vy += dtau;
    // 碰撞测试（含细分）
    let hit = world.solidAt(nx, ny);
    let hitObj = null;
    if (hit !== 1) hitObj = world.objAt(nx, ny);
    if (hit === 1 || hitObj || hit === 2) {
      // 二分细化碰撞点
      let lo = 0, hi = 1;
      let bx = x, by = y, bvx = vx - ax * dtau, bvy = vy - dtau;
      for (let i = 0; i < 12; i++) {
        const mid = (lo + hi) / 2;
        const mx = x + (nx - x) * mid, my = y + (ny - y) * mid;
        let h2 = world.solidAt(mx, my);
        let o2 = h2 === 1 ? null : world.objAt(mx, my);
        if (h2 === 1 || h2 === 2 || o2) { hi = mid; if (h2 === 2) h2 = 2; }
        else { lo = mid; }
      }
      const ix = x + (nx - x) * lo, iy = y + (ny - y) * lo;
      if (hit === 2) return { x: nx, y: ny, oob: true };
      const io = world.objAt(ix, iy);
      if (hitObj) return { x: ix, y: iy, obj: io || hitObj, hitObjFlag: true };
      return { x: ix, y: iy, obj: io };
    }
    px = x; py = y;
    x = nx; y = ny;
    if (tau > 380) break;
  }
  return { x, y, oob: true };
}

// 计算一种攻击的所有落点
function simulateShot(world, kind, angle, power, dir) {
  const gAng = world.actor.groundAngle || 0;
  const A = Math.sqrt(CFG.S_COEF * power * power);
  const m = muzzlePoint(world);
  const offs = kind === 'spread' ? CFG.SPREAD_OFF : [0];
  const reps = kind === 'burst' ? 3 : 1;
  const out = [];
  for (const off of offs) {
    const th = (angle + off + dir * gAng) * Math.PI / 180;
    if (th < -0.2 || th > Math.PI / 2 + 0.2) { out.push({ oob: true, x: m.x, y: m.y, invalid: true }); continue; }
    const r = march(world, m, A, th, dir);
    for (let i = 0; i < reps; i++) out.push(r);
  }
  return out;
}

// ---------------- 物体几何 ----------------
function objDist(o, px, py) {
  if (o.type === 'bug') {
    return Math.max(0, hypot(px - o.x, py - (o.y - o.radius)) - o.radius);
  }
  const hw = (o.width || 28) / 2, ht = (o.height || 28);
  const dx = Math.max(o.x - hw - px, 0, px - (o.x + hw));
  const dy = Math.max(o.y - ht - py, 0, py - o.y);
  return hypot(dx, dy);
}

// 地形遮挡：爆炸隔着地形不造成溅射伤害（规则明确），也避免对墙反复空打
function losClear(world, x0, y0, x1, y1) {
  const dx = x1 - x0, dy = y1 - y0;
  const dist = Math.sqrt(dx * dx + dy * dy);
  if (dist < 1) return true;
  const n = Math.min(60, Math.max(2, Math.ceil(dist / 3)));
  for (let i = 1; i < n; i++) {
    const t = i / n;
    if (world.solidAt(x0 + dx * t, y0 + dy * t) === 1) return false;
  }
  return true;
}
function isTokenType(t) { return t === 'token' || t === 'bigToken' || t === 'pushToken'; }

// 从 (x, y) 起向下找第一个支撑面，返回该实心格顶面的 y；找不到（会掉出地图）返回 null
function surfaceBelowAt(world, x, y) {
  const cell = world.terr.cell;
  const cx = Math.floor(x / cell);
  if (cx < 0 || cx >= world.terr.w) return null;
  let cy = Math.max(0, Math.floor(y / cell));
  for (; cy < world.terr.h; cy++) {
    if (world.terr.grid[cy * world.terr.w + cx]) return cy * cell;
  }
  return null;
}

// ---------------- 局面评估 ----------------
function analyze(world, obs) {
  const tokens = [], chickens = [];
  let bug = null;
  for (const o of world.objects) {
    if (o.removed || o.broken) continue;
    if (o.type === 'bug') bug = o;
    else if (isTokenType(o.type)) tokens.push(o);
    else if (o.type === 'chicken') chickens.push(o);
  }
  const rec = obs.hole && obs.hole.recycle;
  const recC = rec ? { x: (rec.x0 + rec.x1) / 2, y: (rec.y0 + rec.y1) / 2, ...rec } : null;
  const goal = (obs.hole && obs.hole.goal) || 'tokens';
  const inRect = (x, y, r) => x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;
  const bugInRec = !!(bug && rec && inRect(bug.x, bug.y - bug.radius, rec));
  return { tokens, chickens, bug, rec, recC, goal, bugInRec };
}

function bugCenter(b) { return { x: b.x, y: b.y - b.radius }; }

// 评估一次攻击的效果 -> {score, detail}
function evalAttack(world, st, kind, impacts) {
  const tokensRemain = st.tokens.length;
  // 已经回收的 bug 不再算作"要推进去"的任务，避免反复领取完成奖励
  const bugRelevant = st.bug && st.rec && !st.bugInRec && (st.goal === 'bug' || st.goal === 'both');
  const tokenRelevant = tokensRemain > 0 && (st.goal === 'tokens' || st.goal === 'both');
  let wTok = 0, wBug = 0;
  if (tokenRelevant && bugRelevant) { wTok = 0.5; wBug = 0.5; }
  else if (tokenRelevant) { wTok = 1; }
  else if (bugRelevant) { wBug = 1; }
  else { wTok = 0.2; }

  // 汇总每个物体的伤害
  const dmg = new Map(); // id -> 次数/伤害总和（爆发同点只算一次破壳）
  const pushTotal = { x: 0, y: 0 };
  let chickenHit = 0, oobShots = 0, supportGone = false;
  const bugC = st.bug ? bugCenter(st.bug) : null;

  for (const imp of impacts) {
    if (imp.oob) { oobShots++; continue; }
    const base = CFG.DMG[kind] || 120;
    for (const o of world.objects) {
      if (o.removed || o.broken || o.stunned) continue;
      const d = objDist(o, imp.x, imp.y);
      if (o.type === 'chicken') {
        // 尖啸鸡：实测伤害判定比 30px 更宽松，按 48px 危险半径做规避
        if (d < 48) chickenHit++;
        continue;
      }
      if (o.type === 'bug') {
        // bug 的"被推"半径比伤害半径 30 更大（实测爆点离虫心 44px 仍能把虫推走 173px）
        if (d < 62 && losClear(world, imp.x, imp.y, o.x, o.y - o.radius)) {
          const c = bugCenter(o);
          const dx = c.x - imp.x, dy = c.y - imp.y;
          const dd = Math.max(1, hypot(dx, dy));
          const mag = CFG.PUSH_MAX * Math.max(0, 1 - Math.max(0, dd - o.radius) / CFG.PUSH_R);
          pushTotal.x += (dx / dd) * mag;
          pushTotal.y += (dy / dd) * mag;
          const crater = CFG.CRATER[kind] || 32;
          if (hypot(imp.x - o.x, imp.y - o.y) <= crater * 0.85) supportGone = true;
        }
        continue;
      }
      if (d >= CFG.DMG_R) continue;
      // 溅射被地形挡住则无效
      if (o.type === 'bug') {
        if (!losClear(world, imp.x, imp.y, o.x, o.y - o.radius)) continue;
      } else if (!losClear(world, imp.x, imp.y, o.x, o.y - (o.height || 28) / 2)) {
        continue;
      }
      const dmgv = base * (1 - d / CFG.DMG_R);
      if (o.type === 'chicken') { chickenHit++; continue; }
      if (o.type === 'bug') {
        // 推力：远离爆点，近处更强
        const c = bugCenter(o);
        const dx = c.x - imp.x, dy = c.y - imp.y;
        const dd = Math.max(1, hypot(dx, dy));
        const mag = CFG.PUSH_MAX * Math.max(0, 1 - Math.max(0, dd - o.radius) / CFG.PUSH_R);
        pushTotal.x += (dx / dd) * mag;
        pushTotal.y += (dy / dd) * mag;
        // 爆点就在虫脚下（坑半径内）→ 支撑被炸空，虫会直接往下掉
        const crater = CFG.CRATER[kind] || 32;
        if (hypot(imp.x - o.x, imp.y - o.y) <= crater * 0.85) supportGone = true;
      } else {
        const prev = dmg.get(o.id) || { o, total: 0, count: 0 };
        prev.total += dmgv; prev.count++;
        dmg.set(o.id, prev);
      }
    }
  }

  // 目标进度
  let tokProg = 0;
  if (tokensRemain > 0) {
    for (const o of st.tokens) {
      const rec = dmg.get(o.id);
      const hp = o.hp != null ? o.hp : 1;
      const dealt = rec ? (hp <= 1 ? (rec.count > 0 ? 1 : 0) : Math.min(hp, rec.total)) : 0;
      if (hp <= 1) tokProg += dealt > 0 ? 1 : 0;
      else tokProg += Math.min(1, dealt / hp);
    }
    tokProg /= tokensRemain;
  } else tokProg = 0;

  let bugProg = 0, bugDone = false, bugFell = false;
  if (bugRelevant) {
    const oldD = hypot(bugC.x - st.recC.x, bugC.y - st.recC.y);
    // 向下的推力会被地面挡掉，只保留水平/向上分量；支撑被炸空则原地往下掉
    const pxc = supportGone ? pushTotal.x * 0.4 : pushTotal.x;
    const pyc = supportGone ? 4 : Math.min(0, pushTotal.y);
    let ncx = bugC.x + pxc, ncy = bugC.y + pyc;
    // 落地：落到下方第一个支撑面（虫被推下台阶后会掉到更低的平台上）
    const surf = surfaceBelowAt(world, ncx, ncy + st.bug.radius);
    if (surf == null) { bugFell = true; ncx = 1e5; ncy = 1e5; }
    else ncy = surf - st.bug.radius;
    // 进入回收区判定（圆心在矩形内）
    const inRec = ncx >= st.rec.x0 && ncx <= st.rec.x1 && ncy >= st.rec.y0 && ncy <= st.rec.y1;
    if (inRec) { bugProg = 1; bugDone = true; }
    else {
      const newD = hypot(ncx - st.recC.x, ncy - st.recC.y);
      bugProg = clamp((oldD - newD) / Math.max(60, oldD), -1, 1);
    }
  }

  let score = wTok * tokProg + wBug * bugProg;
  const progBase = score; // 纯目标进度（不含任何罚分/奖励）
  score -= chickenHit * 1.8;
  score -= oobShots * 0.25;
  if (bugFell) score -= 1.2; // 把虫打出地图 = 罚 1 杆并放回原位，代价很高，必须避开
  // 接近奖励：这一杆完全没进展时（目标被遮挡等），也倾向于落到离目标更近的位置
  const proxi = [];
  if (tokenRelevant) for (const o of st.tokens) proxi.push({ x: o.x, y: o.y - (o.height || 28) / 2 });
  if (bugRelevant && st.bug) proxi.push({ x: st.bug.x, y: st.bug.y });
  let dMin = 1e9;
  for (const t of proxi) for (const imp of impacts) {
    if (imp.oob) continue;
    dMin = Math.min(dMin, hypot(imp.x - t.x, imp.y - t.y));
  }
  if (dMin < 1e8) score += 0.12 * Math.max(0, 1 - dMin / 700);
  let prog = progBase;
  if (tokProg >= 1 && tokensRemain > 0) { score += 0.35; prog += 0.35; }
  if (bugDone) { score += 0.35; prog += 0.35; }
  // rawScore：不计"伤鸡"罚分的目标进度，用于"实在没别的办法时是否值得付 1 杆代价"的判断
  const rawScore = score + chickenHit * 1.8;
  return { score, rawScore, prog, tokProg, bugProg, chickenHit, bugDone, pushTotal, dmg };
}

// ---------------- 搜索最优动作 ----------------
// 风漂移积分：给定 As = A·sinθ 与 τ，返回水平漂移（只依赖 τ、dy 决定的抛物线高度）
function driftIntegral(m, As, tau, wind) {
  let x = 0, vx = 0;
  const dt = 0.08;
  for (let t = 0; t < tau; t += dt) {
    const step = Math.min(dt, tau - t);
    const y = m.y - As * t + 0.5 * t * t;
    const alt = m.y - y;
    const f = alt > CFG.WIND_ALT ? CFG.WIND_BOOST : 1;
    x += vx * step + 0.5 * wind * f * step * step;
    vx += wind * f * step;
  }
  return x;
}

// 解析求瞄准：找出经过目标点的 (angle, power)
function solveAim(world, target, dir, gAng) {
  const m = muzzlePoint(world);
  const dx = target.x - m.x, dy = target.y - m.y;
  const wind = world.wind * CFG.K0;
  const sols = [];
  for (let tau = 3; tau <= 160; tau += 1.5) {
    const As = (0.5 * tau * tau - dy) / tau;
    if (As < -3) continue;
    const W = driftIntegral(m, As, tau, wind);
    const Ac = (dx - W) / tau;
    const AcS = dir > 0 ? Ac : -Ac;
    if (AcS <= 0.2) continue;
    const A = Math.sqrt(As * As + Ac * Ac);
    const power = A / Math.sqrt(CFG.S_COEF);
    if (power < 4 || power > 100) continue;
    const th = Math.atan2(As, AcS);
    const angle = th * 180 / Math.PI - dir * gAng;
    if (angle < 0 || angle > 85) continue;
    sols.push({ angle, power, dir });
  }
  return sols;
}

function candidateTargets(world, st) {
  const T = [];
  const wantTokens = st.goal === 'tokens' || st.goal === 'both' || st.goal === undefined;
  const wantBug = (st.goal === 'bug' || st.goal === 'both') && st.bug && !st.bugInRec;
  if (wantTokens) {
    for (const t of st.tokens) {
      const hw = (t.width || 28) / 2, ht = (t.height || 28);
      T.push({ x: t.x, y: t.y - ht * 0.55, tag: 'tok' });
      T.push({ x: t.x - hw * 0.5, y: t.y - ht * 0.3, tag: 'tok' });
      T.push({ x: t.x + hw * 0.5, y: t.y - ht * 0.3, tag: 'tok' });
    }
  }
  if (wantBug) {
    const c = bugCenter(st.bug);
    for (const r of [16, 26, 38, 52]) {
      for (let k = 0; k < 12; k++) {
        const a = (k * 30 + 15) * Math.PI / 180;
        T.push({ x: c.x + Math.cos(a) * r, y: c.y + Math.sin(a) * r, tag: 'bug' });
      }
    }
    if (st.recC) {
      // 沿“远离回收区”方向的地面点，用来把虫推向回收区
      const dx = st.recC.x - c.x, dy = st.recC.y - c.y;
      const dd = Math.max(1, hypot(dx, dy));
      for (const off of [14, 22, 32, 44]) {
        T.push({ x: c.x - (dx / dd) * off, y: c.y - (dy / dd) * off, tag: 'bug' });
      }
    }
  }
  return T;
}

function chooseAction(world, obs, deadline, guard) {
  const st = analyze(world, obs);
  const kinds = ['single', 'burst', 'spread'];
  const gAng = world.actor.groundAngle || 0;
  // 防复读：上一杆完全没有改变任何物体、也没有罚杆时，几乎相同的动作再选一次要重罚
  const prev = guard && guard.prevNoChange ? guard.lastAction : null;
  const isRepeat = (c, kind) => prev && prev.kind === kind && prev.dir === c.dir &&
    Math.abs(prev.angle - c.angle) < 0.8 && Math.abs(prev.power - c.power) < 2;
  let best = null;
  let bestSafe = null; // 不伤鸡的最优
  let bestChicken = null; // 伤鸡但进度最高的（按 rawScore）
  const bestKind = {}; // kind -> 该弹种最优
  const consider = (c, kind) => {
    const impacts = simulateShot(world, kind, c.angle, c.power, c.dir);
    const ev = evalAttack(world, st, kind, impacts);
    if (isRepeat(c, kind)) { ev.score -= 0.6; ev.rawScore -= 0.6; }
    const cand = { ...c, kind, ev, impacts };
    if (!best || ev.score > best.ev.score) best = cand;
    if (ev.chickenHit === 0 && (!bestSafe || ev.score > bestSafe.ev.score)) bestSafe = cand;
    if (ev.chickenHit > 0 && (!bestChicken || ev.rawScore > bestChicken.ev.rawScore)) bestChicken = cand;
    if (!bestKind[kind] || ev.score > bestKind[kind].ev.score) bestKind[kind] = cand;
  };

  // 1) 解析瞄准候选：直接瞄准 token / bug 周围点
  for (const t of candidateTargets(world, st)) {
    if (Date.now() > deadline) break;
    for (const dir of [1, -1]) {
      const sols = solveAim(world, t, dir, gAng);
      // 采样：解族从低抛到高抛整段都要覆盖，密集取点（多层地形里能穿过的窗口往往很窄）
      const step = Math.max(1, Math.floor(sols.length / 14));
      for (let i = 0; i < sols.length; i += step) {
        const s = sols[i];
        if (Date.now() > deadline) break;
        for (const kind of kinds) consider(s, kind);
      }
      if (sols.length) { const s = sols[sols.length - 1]; for (const kind of kinds) consider(s, kind); }
    }
  }

  // 2) 粗网格（兜底：打地形、开路、意外角度）
  for (const dir of [1, -1]) {
    for (let angle = 0; angle <= 85; angle += 4) {
      for (let power = 10; power <= 100; power += 8) {
        if (Date.now() > deadline) break;
        for (const kind of kinds) consider({ angle, power, dir }, kind);
      }
    }
  }

  if (!best) return null;

  // 3) 细化：每种弹种围绕自己的最优解加密
  for (const kind of kinds) {
    const b = bestKind[kind];
    if (!b) continue;
    for (const dir of [1, -1]) {
      for (let da = -3; da <= 3; da += 0.5) {
        for (let dp = -6; dp <= 6; dp += 1.5) {
          if (Date.now() > deadline) break;
          const c = { dir, angle: clamp(b.angle + da, 0, 85), power: clamp(b.power + dp, 1, 100) };
          consider(c, kind);
        }
      }
    }
  }
  // 3.5) 完全拿不到进展时（目标被地形挡住）：用掘进弹打"挡住去路的那块地形"开路
  if (best.ev.prog < 0.12) {
    const digs = [];
    for (const t of st.tokens) {
      const tgt = { x: t.x, y: t.y - (t.height || 28) / 2 };
      for (const dir of [1, -1]) {
        if (Date.now() > deadline) break;
        for (const s of solveAim(world, tgt, dir, gAng)) {
          const imp = simulateShot(world, 'dig', s.angle, s.power, dir)[0];
          if (imp.oob) continue;
          digs.push({ ...s, dist: hypot(imp.x - tgt.x, imp.y - tgt.y) });
        }
      }
    }
    digs.sort((a, b) => a.dist - b.dist);
    for (const c of digs.slice(0, 8)) {
      if (Date.now() > deadline) break;
      const impacts = simulateShot(world, 'dig', c.angle, c.power, c.dir);
      const ev = evalAttack(world, st, 'dig', impacts);
      ev.score += 0.45; // 开路价值：掘进弹坑径 60，先打掉挡路地形，下一杆按新地形重算
      if (ev.score > best.ev.score) best = { ...c, kind: 'dig', ev, impacts };
    }
  }

  // 4) 选最终动作：优先不伤鸡；只有当"不伤鸡的方案完全拿不到进展、而伤鸡方案能拿到实质进展"时，
  //    才付 1 杆代价去换（避免目标被鸡卡住时反复空打）
  if (bestSafe) {
    const allowChicken = bestSafe.ev.prog < 0.05 && bestChicken && bestChicken.ev.prog >= 0.3;
    if (allowChicken) {
      best = bestChicken;
      process.stderr.write(`[chicken] 无安全方案，接受 1 杆代价换进度 ${bestChicken.ev.prog.toFixed(2)}\n`);
    } else {
      best = bestSafe;
    }
  }
  return best;
}

// ---------------- 考卷 ----------------
function predictExam(msg) {
  const cfg = msg.config || {};
  const ground = msg.ground != null ? msg.ground : 780;
  const hMuz = (cfg.BODY && cfg.BODY.muzzleHeight) ? cfg.BODY.muzzleHeight : CFG.MUZZLE_H;
  const out = [];
  for (const q of msg.questions || []) {
    const A = Math.sqrt(CFG.S_COEF * q.power * q.power);
    const th = q.angle * Math.PI / 180;
    const ym = ground - hMuz;
    const x0 = q.x;
    const tauEnd = A * Math.sin(th) + Math.sqrt(A * A * Math.sin(th) * Math.sin(th) + 2 * hMuz);
    // 数值积分风漂移
    let x = x0, vx = q.dir * A * Math.cos(th);
    const wind = q.wind * CFG.K0;
    const dtau = 0.02;
    for (let tau = 0; tau < tauEnd; tau += dtau) {
      const step = Math.min(dtau, tauEnd - tau);
      const y = ym - A * Math.sin(th) * tau + 0.5 * tau * tau;
      const alt = ym - y;
      const f = alt > CFG.WIND_ALT ? CFG.WIND_BOOST : 1;
      x += vx * step + 0.5 * wind * f * step * step;
      vx += wind * f * step;
    }
    out.push({ id: q.id, x: Math.round(x * 10) / 10, y: Math.round((ground + 0.1) * 10) / 10 });
  }
  return out;
}

// ---------------- 主循环 ----------------
let lastObs = null;
let lastAction = null; // 本洞内上一杆的动作（只用于防复读，不跨洞、不按洞区分）

// 自检：node bot/main.mjs --selftest  （不参与正式协议，仅本地验证物理与瞄准）
if (process.argv.includes('--selftest')) {
  const flat = {
    width: 400, height: 225, cell: 4,
    rle: [[0, 78000], [1, 12000]],
  };
  const world = makeWorld({
    terrain: flat, wind: 0, hole: { wind: 0, goal: 'tokens' },
    actor: { x: 200, y: 768, groundAngle: 0 },
    objects: [{ id: 0, type: 'token', x: 821, y: 780, width: 28, height: 28, hp: 1 }],
  });
  const m = muzzlePoint(world);
  console.log('muzzle', m);
  for (const [tx, ty, wind] of [[821.44, 780.1, 0], [459.41, 780.1, 0], [1334.84, 780.1, 0], [443.59, 780.1, -8]]) {
    world.wind = wind;
    const sols = solveAim(world, { x: tx, y: ty }, 1, 0);
    const s = sols[Math.floor(sols.length / 2)];
    const all = sols.map(z => `${z.angle.toFixed(1)}/${z.power.toFixed(1)}`).join(' ');
    let bestErr = 999, bp = null;
    for (const z of sols) {
      const imp = simulateShot(world, 'single', z.angle, z.power, 1)[0];
      const err = Math.hypot(imp.x - tx, imp.y - ty);
      if (err < bestErr) { bestErr = err; bp = z; }
    }
    console.log(`target(${tx},${ty}) wind=${wind} sols=${sols.length} [${all}] best=${bp && bp.angle.toFixed(2)}/${bp && bp.power.toFixed(2)} err=${bestErr.toFixed(2)}`);
  }
  process.exit(0);
}

function respond(msg) {
  const type = msg.type;
  if (type === 'init') {
    lastObs = msg;
    lastAction = null;
    return { ok: true };
  }
  if (type === 'exam') {
    try { return { predictions: predictExam(msg) }; }
    catch (e) { return { predictions: [] }; }
  }
  if (type === 'turn') {
    lastObs = msg;
    const t0 = Date.now();
    try {
      const world = makeWorld(msg);
      // 上一杆结果：物体没变化、也没罚杆 → 这一杆基本没起作用，避免原样复读
      const lr = msg.lastResult;
      const prevNoChange = !!(lastAction && lr && (lr.objectChanges || []).length === 0 && (lr.penalties || []).length === 0);
      const best = chooseAction(world, msg, t0 + 520, { lastAction, prevNoChange });
      if (best) {
        const angle = Math.round(best.angle * 4) / 4;
        const power = Math.round(best.power * 2) / 2;
        const impStr = (best.impacts || []).map(i => i.oob ? 'OOB' : `(${i.x.toFixed(0)},${i.y.toFixed(0)}${i.obj ? '#' + i.obj.id : ''})`).join(' ');
        process.stderr.write(`[plan] ${best.kind} a=${angle} p=${power} d=${best.dir} score=${best.ev.score.toFixed(3)} tok=${best.ev.tokProg.toFixed(2)} bug=${best.ev.bugProg.toFixed(2)} push=(${best.ev.pushTotal.x.toFixed(0)},${best.ev.pushTotal.y.toFixed(0)}) imp=${impStr} rep=${prevNoChange ? 1 : 0} t=${Date.now() - t0}ms\n`);
        lastAction = { kind: best.kind, angle, power, dir: best.dir };
        return { attack: { kind: best.kind, angle, power, dir: best.dir } };
      }
    } catch (e) {
      process.stderr.write(`[err] ${e && e.stack || e}\n`);
    }
    // 兜底：朝目标方向打一发 45°
    return { attack: { kind: 'single', angle: 45, power: 60, dir: 1 } };
  }
  return {};
}

// 协议循环：始终启用（正式运行由主控通过 stdin 逐行喂 JSON）
const rl = createInterface({ input: process.stdin });
rl.on('line', line => {
  line = line.trim();
  if (!line) return;
  let msg, reply;
  try { msg = JSON.parse(line); } catch (e) { return; }
  try { reply = respond(msg); } catch (e) { reply = {}; }
  process.stdout.write(JSON.stringify({ reqId: msg.reqId, ...reply }) + '\n');
});

export { CFG, makeWorld, muzzlePoint, march, simulateShot, objDist, analyze, evalAttack, solveAim, chooseAction, predictExam, driftIntegral };
