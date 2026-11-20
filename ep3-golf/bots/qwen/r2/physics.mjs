// 物理模型：由试射拟合。坐标 y 向下，世界 1600x900，地面参考 y=780。
export const PHYS = {
  C: 15.0,          // 初速 px/s = C * power（试射拟合，残差<0.3px）
  G: 1873.83,       // 重力 px/s^2（同上）
  MUZZLE: 35,       // 炮口高于脚底（config.BODY.muzzleHeight）
  WIND_K: 70.15,    // 风加速度 = wind * K * clamp((900-y)/S, 1, M)
  WIND_S: 422.68,   // y<477 起随高度增强，y<327 饱和（16 发试射，有风最大残差 2.5px）
  WIND_M: 1.355,
};

export function windAccel(y, wind, p = PHYS) {
  return wind * p.WIND_K * Math.min(p.WIND_M, Math.max(1, (900 - y) / p.WIND_S));
}

// 数值积分弹道。solid(x,y) 返回该像素是否实心；objects 可选 [{x,y,w,h,r}]（底部中心）。
// 返回 {x, y, hit:'ground'|'object'|'out', objectId, t, path?}
export function simulate(opts, p = PHYS) {
  const { x0, y0, angle, power, dir, wind = 0, solid, objects = [], offset = 0, wantPath = false, windFn } = opts;
  const wa = windFn || (yy => windAccel(yy, wind, p));
  const a = (angle + offset) * Math.PI / 180;
  const v0 = p.C * power;
  let vx = dir * v0 * Math.cos(a);
  let vy = -v0 * Math.sin(a);
  let x = x0, y = y0, t = 0;
  const dt = 1 / 120;
  const path = wantPath ? [{ x, y }] : null;
  const blocked = (xx, yy) => {
    if (solid && solid(xx, yy)) return true;
    for (const o of objects) {
      if (o.r != null) {
        const dx = xx - o.x, dy = yy - (o.y - o.r);
        if (dx * dx + dy * dy <= o.r * o.r) return true;
      } else if (xx >= o.x - o.w / 2 && xx <= o.x + o.w / 2 && yy >= o.y - o.h && yy <= o.y) return true;
    }
    return false;
  };
  const objAt = (xx, yy) => {
    for (const o of objects) {
      if (o.r != null) {
        const dx = xx - o.x, dy = yy - (o.y - o.r);
        if (dx * dx + dy * dy <= o.r * o.r) return o.id;
      } else if (xx >= o.x - o.w / 2 && xx <= o.x + o.w / 2 && yy >= o.y - o.h && yy <= o.y) return o.id;
    }
    return undefined;
  };
  for (let i = 0; i < 10000; i++) {
    const ax = wa(y, wind);
    const nx = x + vx * dt + 0.5 * ax * dt * dt;
    const ny = y + vy * dt + 0.5 * p.G * dt * dt;
    const nvx = vx + ax * dt, nvy = vy + p.G * dt;
    if (nx < 0 || nx > 1600 || ny > 900) return { x: nx, y: ny, hit: 'out', t: t + dt, path };
    if (blocked(nx, ny)) {
      // 步内二分找精确交点
      let lo = 0, hi = 1, bx = x, by = y;
      for (let k = 0; k < 10; k++) {
        const m = (lo + hi) / 2;
        const mx = x + (nx - x) * m, my = y + (ny - y) * m;
        if (blocked(mx, my)) { hi = m; bx = mx; by = my; } else { lo = m; bx = mx; by = my; }
      }
      const oid = objAt(bx, by);
      return { x: bx, y: by, hit: oid != null ? 'object' : 'ground', objectId: oid, t: t + dt * hi, path };
    }
    x = nx; y = ny; vx = nvx; vy = nvy; t += dt;
    if (wantPath && i % 2 === 0) path.push({ x, y });
  }
  return { x, y, hit: 'out', t, path };
}

// 平地解析落点（无风），用于快速估距
export function flatRange(angle, power, dh = 35, p = PHYS) {
  const a = angle * Math.PI / 180, v0 = p.C * power;
  const vx = v0 * Math.cos(a), vy = v0 * Math.sin(a);
  const t = (vy + Math.sqrt(vy * vy + 2 * p.G * dh)) / p.G;
  return vx * t;
}