// 物理模型与弹道数值模拟。
// 参数经试射拟合（见 NOTES.md / fit.mjs），供 Bot 反解瞄准与考卷落点预测。
// y 轴向下。风只作用于水平方向，强度随高度线性变化。

// 拟合参数（会被 fit.mjs 更新；Bot 运行时使用这里的值）
export const PARAMS = {
  a: 10.523,    // v0 = a * power  (px/s per power unit) — 试射拟合
  vExp: 1.0,    // v0 = a * power^vExp — 拟合≈1.0，确认线性
  g: 924.2,     // 重力加速度 px/s^2 — 试射拟合
  H: 38.96,     // 炮口离脚底高度 px — 试射拟合（≈muzzleHeight 35 + 积分修正）
  w0: 34.46,    // 近地水平风加速度基数：ax = wind*(w0 + w1*relu(wYThresh - y))
  w1: 0.0471,   // 高空风增强梯度（每像素）— 试射拟合
  wYThresh: 490,// 该 y 以上（更高空）风开始线性增强；y>=490 为常量风
};

export const YREF = 780;   // 平地地面 y（发射脚底参考）

// 风水平加速度：近地常量，高空（y<wYThresh）线性增强
export function windAx(P, y, wind) {
  return wind * (P.w0 + P.w1 * Math.max(0, P.wYThresh - y));
}
export const DT = 1 / 240; // 积分步长 s
export const MAXT = 8;     // 最大飞行时间 s
export const W = 1600, HGT = 900, CELL = 4;

// 解析游程编码
export function decodeTerrain(terrain) {
  const w = terrain.width, h = terrain.height, cell = terrain.cell;
  const grid = new Uint8Array(w * h);
  let idx = 0;
  for (const [val, count] of terrain.rle) {
    for (let k = 0; k < count; k++) grid[idx++] = val ? 1 : 0;
  }
  return { w, h, cell, grid };
}

// (px,py) 是否落在实心格
export function isSolid(t, px, py) {
  const cx = Math.floor(px / t.cell), cy = Math.floor(py / t.cell);
  if (cy < 0) return false;                 // 地图上方：空气
  if (cx < 0 || cx >= t.w) return false;   // 左右越界：视为空气（另行判断出界）
  if (cy >= t.h) return true;              // 地图下方：实心（掉落）
  return t.grid[cy * t.w + cx] === 1;
}

// 求某列 x 的地面脚底高度（自顶向下第一个实心格的顶边）。返回 y。
export function groundY(t, px) {
  const cx = Math.floor(px / t.cell);
  if (cx < 0 || cx >= t.w) return YREF;
  for (let cy = 0; cy < t.h; cy++) {
    if (t.grid[cy * t.w + cx] === 1) return cy * t.cell;
  }
  return HGT;
}

// 爆炸点与目标中心之间是否被地形遮挡（线段采样）
export function blocked(t, x0, y0, x1, y1) {
  const dx = x1 - x0, dy = y1 - y0;
  const n = Math.ceil(Math.hypot(dx, dy) / (CELL * 0.8));
  for (let i = 1; i < n; i++) {
    const f = i / n;
    if (isSolid(t, x0 + dx * f, y0 + dy * f)) return true;
  }
  return false;
}

// 物体盒子/圆：返回中心与半尺寸
export function objGeom(o) {
  if (o.type === 'bug') {
    const r = o.radius ?? 14;
    return { cx: o.x, cy: o.y - r, r, w: 2 * r, h: 2 * r, circle: true };
  }
  const w = o.width ?? 28, h = o.height ?? 28;
  return { cx: o.x, cy: o.y - h / 2, w, h, circle: false };
}

// 点是否命中某物体（用于"物体挡炮弹"）
function hitObj(o, x, y) {
  const g = objGeom(o);
  if (g.circle) return Math.hypot(x - g.cx, y - g.cy) <= g.r + 1;
  return x >= g.cx - g.w / 2 - 1 && x <= g.cx + g.w / 2 + 1 &&
         y >= g.cy - g.h / 2 - 1 && y <= g.cy + g.h / 2 + 1;
}

// 活动物体（会挡炮弹 / 可被伤害）
export function activeObjects(objects) {
  return (objects || []).filter(o => !o.removed && !o.broken && !o.stunned && !o.inRecycle);
}

// 单条弹道积分，返回首个碰撞点 {x,y,objectId|null,off}。dt 可放大以加速搜索。
export function integrate(P, x0, y0, vx0, vy0, wind, t, objects, dt = DT) {
  const act = objects || [];
  let x = x0, y = y0, vx = vx0, vy = vy0;
  const n = Math.ceil(MAXT / dt);
  for (let i = 0; i < n; i++) {
    const ax = windAx(P, y, wind);
    vx += ax * dt;
    vy += P.g * dt;
    x += vx * dt;
    y += vy * dt;
    if (x < 0 || x > W || y > HGT) return { x, y, objectId: null, off: true };
    // 先看物体（物体在地形之上）
    for (const o of act) {
      if (hitObj(o, x, y)) return { x, y, objectId: o.id, off: false };
    }
    if (isSolid(t, x, y)) return { x, y, objectId: null, off: false };
  }
  return { x, y, objectId: null, off: true };
}

// 各弹种的子弹角度偏移
const OFFSETS = {
  single: [0],
  burst: [0, 0, 0],
  spread: [-6, 0, 6],
  dig: [0],
};

// 计算发射点。footX/footY 为脚底中心；H 为拟合的离地炮口高度。
// 坡地上的微小水平偏移忽略（属已知近似，靠落点反馈纠偏）。
export function muzzle(P, footX, footY, groundAngle) {
  return { x: footX, y: footY - P.H };
}

// 发射速度分量
export function velocity(P, angle, power, dir, groundAngle) {
  const v0 = P.a * Math.pow(power, P.vExp);
  const th = (angle + dir * (groundAngle || 0)) * Math.PI / 180;
  return {
    vx: dir * v0 * Math.cos(th),
    vy: -v0 * Math.sin(th),
  };
}

// 模拟一杆：返回所有子弹的碰撞点数组
export function shotImpacts(P, shot, t, objects, dt = DT) {
  const { footX, footY, groundAngle = 0, angle, power, dir, kind = 'single', wind = 0 } = shot;
  const m = muzzle(P, footX, footY, groundAngle);
  const offs = OFFSETS[kind] || OFFSETS.single;
  const out = [];
  for (const ao of offs) {
    const v = velocity(P, angle + ao, power, dir, groundAngle);
    const r = integrate(P, m.x, m.y, v.vx, v.vy, wind, t, objects, dt);
    out.push(r);
  }
  return out;
}

// 平地（地面 y=780、无物体）落点预测，用于考卷 / 试射拟合。
// 插值求轨迹与 y=780 的交点，x 更精确（考卷按水平误差排名）。
export function flatLanding(P, q) {
  const { x, angle, power, dir, wind = 0, ground = YREF } = q;
  const footY = ground;
  const m = muzzle(P, x, footY, 0);
  const v = velocity(P, angle, power, dir, 0);
  let px = m.x, py = m.y, vx = v.vx, vy = v.vy;
  const n = Math.ceil(MAXT / DT);
  for (let i = 0; i < n; i++) {
    const ax = windAx(P, py, wind);
    vx += ax * DT; vy += P.g * DT;
    const nx = px + vx * DT, ny = py + vy * DT;
    if (vy > 0 && ny >= ground && py < ground) {
      const f = (ground - py) / (ny - py);
      return { x: px + (nx - px) * f, y: ground + 0.2 };
    }
    px = nx; py = ny;
    if (px < 0 || px > W) return { x: px, y: ground + 0.2 };
  }
  return { x: px, y: ground + 0.2 };
}

// 供 fit / 瞄准复用：用一个可用 isSolid(t,...) 的"平地"对象
export function flatTerrain() {
  const t = decodeTerrain({ width: 400, height: 225, cell: 4, rle: [[0, 78000], [1, 12000]] });
  return t;
}
