// 弹弹高尔夫 Bot —— 物理常量、地形解码、弹道求解与飞行模拟。
// 常量由 10 发试射拟合得到（预测 vs 实测 rms ≈ 0.11 px）。

export const V_K = 10.39916;     // 初速 = V_K * power (px/s)
export const G = 900.586;        // 重力加速度 (px/s²)
export const MUZZLE_H = 34.976;  // 炮口离脚底的高度 (px)
export const WIND_A = 33.792;    // 水平风加速度 = WIND_A * wind (px/s²)
export const DMG_R = 30;         // 溅射伤害半径

export function decodeTerrain(terrain) {
  const W = terrain.width | 0, H = terrain.height | 0, cell = terrain.cell || 4;
  const grid = new Uint8Array(W * H);
  let i = 0;
  const rle = terrain.rle || [];
  const pairs = Array.isArray(rle[0]) ? rle : [[rle[0], rle[1]]];
  for (const [v, c] of pairs) {
    for (let j = 0; j < c; j++) grid[i++] = v ? 1 : 0;
  }
  return { grid, W, H, cell };
}

export function solidAt(T, x, y) {
  const cx = Math.floor(x / T.cell), cy = Math.floor(y / T.cell);
  if (cx < 0 || cx >= T.W || cy < 0 || cy >= T.H) return false;
  return T.grid[cy * T.W + cx] === 1;
}

// 某列最上方的实心地表高度（y，向下增大）；null = 无实心
export function surfaceY(T, x) {
  const cx = Math.max(0, Math.min(T.W - 1, Math.floor(x / T.cell)));
  for (let cy = 0; cy < T.H; cy++) if (T.grid[cy * T.W + cx]) return cy * T.cell;
  return null;
}

export function objCenter(o) {
  if (o.type === 'bug') return { x: o.x, y: o.y - (o.radius || 14) };
  const h = o.height || 28;
  return { x: o.x, y: o.y - h / 2 };
}

export function isGone(o) {
  return !o || o.removed || o.broken || o.inRecycle || o.stunned;
}

export function inRect(x, y, r) {
  if (!r) return false;
  return x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;
}

/**
 * 发射向量。angle 为相对身体的仰角(0~85)，dir=±1 选择左右，ga 为地面倾角。
 * 世界仰角 E = angle + dir*ga（dir=+1 往右）；水平分量 = v*cos(E)*dir。
 */
export function launch(angle, power, dir, ga) {
  const v = V_K * power;
  const E = (angle + dir * ga) * Math.PI / 180;
  return { vx: v * Math.cos(E) * dir, vy: v * Math.sin(E), v, E };
}

/**
 * 解析求解：给定炮口 (mx,my) 与目标点 (tx,ty)，求能命中该点的 (angle,power,dir)。
 * 物理：x = mx + vx t + 0.5 a t²， y = my - vy t + 0.5 g t²
 * 在镜像坐标系中求解（dir=-1 时镜像），返回全部有效解。
 */
export function solveToPoint(mx, my, tx, ty, dir, aw, ga, opts = {}) {
  const maxPower = opts.maxPower == null ? 100 : opts.maxPower;
  const minPower = opts.minPower == null ? 1 : opts.minPower;
  const maxAngle = opts.maxAngle == null ? 85 : opts.maxAngle;
  const minAngle = opts.minAngle == null ? 0 : opts.minAngle;
  const step = opts.step || 1;
  const a = dir * aw;               // 镜像坐标系中的风加速度
  const dxm = dir * tx - dir * mx;  // 镜像水平距离
  const res = [];
  for (let E = minAngle; E <= maxAngle + 1e-9; E += step) {
    if (E >= 89.5) break;
    const tan = Math.tan(E * Math.PI / 180);
    const den = 0.5 * (G + tan * a);
    const num = tan * dxm - (my - ty);
    if (den <= 1e-9) continue;
    const t2 = num / den;
    if (t2 <= 0) continue;
    const t = Math.sqrt(t2);
    const vx = (dxm - 0.5 * a * t * t) / t;
    if (vx <= 0) continue;
    const vy = vx * tan;
    const v = Math.hypot(vx, vy);
    const power = v / V_K;
    if (power < minPower || power > maxPower) continue;
    // 引擎参数：世界仰角 E = angle + dir*ga  →  angle = E - dir*ga
    const angle = E - dir * ga;
    if (angle < -1e-6 || angle > maxAngle + 1e-6) continue;
    res.push({ angle: Math.max(0, Math.min(85, angle)), power, dir, t, E });
  }
  return res;
}

/** 线段与圆是否相交（用于连续碰撞检测，避免高速炮弹穿透薄物体） */
function segCircle(x1, y1, x2, y2, cx, cy, r) {
  const dx = x2 - x1, dy = y2 - y1;
  const fx = x1 - cx, fy = y1 - cy;
  const a = dx * dx + dy * dy;
  if (a < 1e-9) return fx * fx + fy * fy <= r * r;
  const b = 2 * (fx * dx + fy * dy);
  const c = fx * fx + fy * fy - r * r;
  const disc = b * b - 4 * a * c;
  if (disc < 0) return false;
  const sq = Math.sqrt(disc);
  const t1 = (-b - sq) / (2 * a), t2 = (-b + sq) / (2 * a);
  return (t1 >= 0 && t1 <= 1) || (t2 >= 0 && t2 <= 1) || (t1 < 0 && t2 > 1);
}

/** 线段与轴对齐矩形是否相交 */
function segRect(x1, y1, x2, y2, cx, cy, hw, hh) {
  // Liang-Barsky
  let t0 = 0, t1 = 1;
  const dx = x2 - x1, dy = y2 - y1;
  const p = [-dx, dx, -dy, dy];
  const q = [x1 - (cx - hw), (cx + hw) - x1, y1 - (cy - hh), (cy + hh) - y1];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) { if (q[i] < 0) return false; continue; }
    const r = q[i] / p[i];
    if (p[i] < 0) { if (r > t1) return false; if (r > t0) t0 = r; }
    else { if (r < t0) return false; if (r < t1) t1 = r; }
  }
  return true;
}

/**
 * 模拟一发炮弹直到撞地形/物体/出界（连续碰撞检测）。
 * env: {T, objects, wind}
 * 返回 {hit:'terrain'|'object'|'out', x, y, objId, t}
 */
export function simulate(env, shot) {
  const T = env.T;
  const { vx, vy } = launch(shot.angle, shot.power, shot.dir, env.ga || 0);
  const aw = WIND_A * env.wind;
  const mx = shot.mx, my = shot.my;
  const dt = 0.010, tMax = 6;
  let px = mx, py = my;
  for (let t = dt; t <= tMax; t += dt) {
    const x = mx + vx * t + 0.5 * aw * t * t;
    const y = my - vy * t + 0.5 * G * t * t;
    // 物体（挡炮弹）—— 用线段判定，避免高速时穿透
    for (const o of env.objects) {
      if (isGone(o)) continue;
      const c = objCenter(o);
      if (o.type === 'bug') {
        const r = o.radius || 14;
        if (segCircle(px, py, x, y, c.x, c.y, r)) return { hit: 'object', x, y, objId: o.id, t };
      } else {
        const w = (o.width || 28) / 2, h = (o.height || 28) / 2;
        if (segRect(px, py, x, y, c.x, c.y, w, h)) return { hit: 'object', x, y, objId: o.id, t };
      }
    }
    // 地形：沿线段找第一个实心格
    if (segHitsTerrain(T, px, py, x, y)) {
      const hit = firstTerrainHit(T, px, py, x, y);
      return { hit: 'terrain', x: hit.x, y: hit.y, objId: null, t };
    }
    if (x < -80 || x > 1680 || y > 980) return { hit: 'out', x, y, objId: null, t };
    px = x; py = y;
  }
  return { hit: 'out', x: px, y: py, objId: null, t: tMax };
}

/** 线段是否穿过实心地形 */
function segHitsTerrain(T, x1, y1, x2, y2) {
  const steps = Math.max(2, Math.ceil(Math.hypot(x2 - x1, y2 - y1) / (T.cell * 0.5)));
  for (let i = 1; i <= steps; i++) {
    const f = i / steps;
    const x = x1 + (x2 - x1) * f, y = y1 + (y2 - y1) * f;
    if (solidAt(T, x, y)) return true;
  }
  return false;
}

/** 在线段上找第一个进入实心地形的点 */
function firstTerrainHit(T, x1, y1, x2, y2) {
  const steps = Math.max(2, Math.ceil(Math.hypot(x2 - x1, y2 - y1) / (T.cell * 0.5)));
  let lo = 0, hi = 1, prev = { x: x1, y: y1 };
  for (let i = 1; i <= steps; i++) {
    const f = i / steps;
    const x = x1 + (x2 - x1) * f, y = y1 + (y2 - y1) * f;
    if (solidAt(T, x, y)) {
      lo = (i - 1) / steps; hi = f;
      for (let k = 0; k < 12; k++) {
        const mid = (lo + hi) / 2;
        const mx2 = x1 + (x2 - x1) * mid, my2 = y1 + (y2 - y1) * mid;
        if (solidAt(T, mx2, my2)) hi = mid; else lo = mid;
      }
      return { x: x1 + (x2 - x1) * lo, y: y1 + (y2 - y1) * lo };
    }
    prev = { x, y };
  }
  return prev;
}

/** 爆炸溅射：返回 Map<id, damage>（直线衰减） */
export function blastDamage(env, px, py, base) {
  const out = new Map();
  for (const o of env.objects) {
    if (isGone(o)) continue;
    const c = objCenter(o);
    const d = Math.hypot(px - c.x, py - c.y);
    if (d < DMG_R) {
      const dm = base * (1 - d / DMG_R);
      if (dm > 0.5) out.set(o.id, dm);
    }
  }
  return out;
}

/** 落点预测（用于考卷 / 平地估算）：给定平地高度 gy */
export function predictLanding(x, gy, angle, power, dir, wind, muzzleUp = MUZZLE_H) {
  const { vx, vy } = launch(angle, power, dir, 0);
  const aw = WIND_A * wind;
  const my = gy - muzzleUp;
  // gy = my - vy t + 0.5 g t²
  const disc = vy * vy + 2 * G * (gy - my);
  if (disc < 0) return { x, y: gy };
  const t = (vy + Math.sqrt(disc)) / G;
  return { x: x + vx * t + 0.5 * aw * t * t, y: gy };
}
