// 物理模块：弹道模拟（参数来自试射拟合，见 NOTES.md）
// 模型（标度：g=600，真实系统按 (v,g,风) 同比缩放后空间弹道一致）：
//   初速 v = K * power（K=8.488）
//   重力 g = 600（y 向下）
//   风水平加速度 ax = C0 * wind * f(y)，f(y)= y<390 ? 1.36 : 1.0
//   发射点：脚底(actor.y+footOffset) 沿身体法线上移 muzzleHeight；平地即 (x, footY-35)
//   实际发射角 = angle + dir*groundAngle（相对水平面）
export const PHYS = {
  K: 8.488,      // v = K * power
  G: 600,        // 重力（标度值）
  C0: 22.56,     // 风系数
  // 风高度剖面（16 发试射拟合，rms 1.26px）：
  // y≥440 系数 1.0；385≤y<440 微增 1.05；y<385 高空强风 1.36
  W1: 440, W2: 385,
};

export function windFactor(y) {
  if (y < PHYS.W2) return 1.36;
  if (y < PHYS.W1) return 1.05;
  return 1.0;
}

// 地形网格：rle [值,个数] 按行展开，width×height 格，每格 cell 像素
export class Terrain {
  constructor(t) {
    this.w = t.width; this.h = t.height; this.cell = t.cell;
    this.g = new Uint8Array(this.w * this.h);
    let i = 0;
    if (t.rle.length && Array.isArray(t.rle[0])) {
      // 嵌套对 [[值,个数],...]
      for (const pair of t.rle) {
        const val = pair[0], n = pair[1];
        for (let j = 0; j < n; j++) this.g[i++] = val;
      }
    } else {
      // 平铺 [值,个数,值,个数,...]
      for (let k = 0; k < t.rle.length; k += 2) {
        const val = t.rle[k], n = t.rle[k + 1];
        for (let j = 0; j < n; j++) this.g[i++] = val;
      }
    }
  }
  solid(px, py) {
    if (py < 0) return false;
    if (py >= this.h * this.cell) return true; // 底部以下视为实心
    if (px < 0 || px >= this.w * this.cell) return false;
    return this.g[((py / this.cell) | 0) * this.w + ((px / this.cell) | 0)] === 1;
  }
  // 顶部实心表面的 y（用于找站立面/物体支撑），无则返回 null
  surfaceY(px) {
    const cx = (px / this.cell) | 0;
    if (cx < 0 || cx >= this.w) return null;
    for (let cy = 0; cy < this.h; cy++) if (this.g[cy * this.w + cx]) return cy * this.cell;
    return null;
  }
}

// 模拟一发炮弹。返回 {x,y,hit,out,steps} 或提前命中物体时 {hitObject}
// objs: 可选 [{x,y,w,h,r}] 待撞物体（圆形用 r，矩形用 w/h，底部中心坐标）
// margin: 地形安全裕度（像素），>0 时把地形向上/左右膨胀，用于避开刀刃边缘弹道
export function simulateShot(opts) {
  const { terrain, wind, fromX, fromY, angleDeg, power, dir, objs, maxT = 12, dt = 1 / 480, margin = 0 } = opts;
  const v = PHYS.K * power;
  const a = angleDeg * Math.PI / 180;
  let vx = dir * v * Math.cos(a), vy = -v * Math.sin(a);
  let px = fromX, py = fromY, t = 0;
  let prevX = px, prevY = py;
  for (let i = 0; i < maxT * 480; i++) {
    const f = windFactor(py);
    const ax = PHYS.C0 * wind * f;
    const nvx = vx + ax * dt, nvy = vy + PHYS.G * dt;
    prevX = px; prevY = py;
    px += (vx + nvx) / 2 * dt; py += (vy + nvy) / 2 * dt;
    vx = nvx; vy = nvy; t += dt;
    // 出左右边界
    if (px < 0 || px > 1600) return { x: px, y: py, out: true, t };
    // 撞物体
    if (objs) for (const o of objs) {
      if (o.r != null) {
        const dx = px - o.x, dy = py - (o.y - o.r);
        if (dx * dx + dy * dy < o.r * o.r) return { x: px, y: py, hitObject: o, t };
      } else {
        if (Math.abs(px - o.x) <= o.w / 2 && py >= o.y - o.h && py <= o.y) return { x: px, y: py, hitObject: o, t };
      }
    }
    // 撞地形：二分回退找穿透点（含安全裕度检查）
    const blocked = terrain.solid(px, py) ||
      (margin > 0 && (terrain.solid(px, py + margin) || terrain.solid(px + margin, py) || terrain.solid(px - margin, py)));
    if (blocked) {
      const blk = (x, y) => terrain.solid(x, y) ||
        (margin > 0 && (terrain.solid(x, y + margin) || terrain.solid(x + margin, y) || terrain.solid(x - margin, y)));
      let bx = prevX, by = prevY;
      for (let s = 0; s < 8; s++) {
        const mx = (bx + px) / 2, my = (by + py) / 2;
        if (blk(mx, my)) { px = mx; py = my; } else { bx = mx; by = my; }
      }
      return { x: px, y: py, hit: 'terrain', t };
    }
  }
  return { x: px, y: py, out: true, t };
}

function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

// 平地快速预测（考卷/试射用）：地面 y=ground，炮口 (x, ground-35)
export function predictFlat(wind, x, angle, power, dir, ground = 780) {
  const v = PHYS.K * power, a = angle * Math.PI / 180;
  let vx = dir * v * Math.cos(a), vy = -v * Math.sin(a);
  let px = x, py = ground - 35, t = 0;
  const dt = 1 / 480;
  let prevX = px, prevY = py;
  for (let i = 0; i < 12 * 480; i++) {
    const ax = PHYS.C0 * wind * windFactor(py);
    const nvx = vx + ax * dt, nvy = vy + PHYS.G * dt;
    prevX = px; prevY = py;
    px += (vx + nvx) / 2 * dt; py += (vy + nvy) / 2 * dt;
    vx = nvx; vy = nvy; t += dt;
    if (px < 0 || px > 1600) return { x: px, y: py, out: true };
    if (py >= ground) {
      const frac = (ground - prevY) / (py - prevY);
      return { x: prevX + frac * (px - prevX), y: ground };
    }
  }
  return { x: px, y: py, out: true };
}
