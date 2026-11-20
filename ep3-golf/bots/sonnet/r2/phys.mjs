// 物理模型（试射拟合）：g=1 的无量纲单位，v = sqrt(0.12)*power（D=v^2/g=0.12 p^2）
// 风加速度 = K * wind * windF(y)，windF 在高空放大（拟合值）。
export const P = {
  D: 0.12, K: 0.037512, YTH: 477.9, SPAN: 152.6, BETA: 0.0023,
  foot: 12, muzzleH: 35,
};
export function windF(y) { return 1 + P.BETA * Math.min(P.SPAN, Math.max(0, P.YTH - y)); }

export function decodeTerrain(t) {
  const g = new Uint8Array(t.width * t.height);
  let i = 0;
  for (const [v, n] of t.rle) { if (v) g.fill(1, i, i + n); i += n; }
  return { w: t.width, h: t.height, g };
}
export function solidAt(T, x, y) {
  const cx = Math.floor(x / 4), cy = Math.floor(y / 4);
  if (cx < 0 || cx >= T.w) return false;
  if (cy < 0) return false;
  if (cy >= T.h) return true;
  return T.g[cy * T.w + cx] === 1;
}
export function objBox(o) {
  if (o.type === 'bug') { const r = o.radius ?? 14; return { x0: o.x - r, x1: o.x + r, y0: o.y - 2 * r, y1: o.y }; }
  const w = o.width ?? 28, h = o.height ?? 28;
  return { x0: o.x - w / 2, x1: o.x + w / 2, y0: o.y - h, y1: o.y };
}
export function objActive(o) { return !o.removed && !o.broken; }

// 模拟一发炮弹。返回 {x,y,obj,out,t,path?}
// opts: dt, objs(阻挡物体列表), wind, T(地形)
export function flight(T, objs, wind, mx, my, thetaEff, dir, power, dt = 0.05, keepPath = false) {
  const v = Math.sqrt(P.D) * power;
  const th = thetaEff * Math.PI / 180;
  let vx = dir * v * Math.cos(th), vy = -v * Math.sin(th);
  let x = mx, y = my, t = 0;
  const boxes = objs.map(o => ({ o, b: objBox(o) }));
  const path = keepPath ? [[x, y]] : null;
  for (let i = 0; i < 20000; i++) {
    const ax = P.K * wind * windF(y);
    const nx = x + vx * dt + 0.5 * ax * dt * dt;
    const ny = y + vy * dt + 0.5 * dt * dt;
    const dx = nx - x, dy = ny - y;
    const n = Math.max(1, Math.ceil(Math.max(Math.abs(dx), Math.abs(dy)) / 2));
    for (let s = 1; s <= n; s++) {
      const px = x + dx * s / n, py = y + dy * s / n;
      if (px < 0 || px > 1600) return { x: Math.min(1600, Math.max(0, px)), y: py, out: true, t, path };
      if (py > 900) return { x: px, y: py, out: true, t, path };
      if (solidAt(T, px, py)) {
        // 回退到进入前一点，取精细点
        return { x: px, y: py, terrain: true, t: t + dt * s / n, path };
      }
      for (const { o, b } of boxes) {
        if (px >= b.x0 && px <= b.x1 && py >= b.y0 && py <= b.y1) {
          return { x: px, y: py, obj: o.id, t: t + dt * s / n, path };
        }
      }
    }
    vx += ax * dt; vy += dt; x = nx; y = ny; t += dt;
    if (path) path.push([x, y]);
  }
  return { x, y, out: true, t, path };
}

// 站位：脚底 y（表面）
export function surfaceY(T, x, nearY, up = 24, down = 24) {
  // 从 nearY-up 向下找：空气格上方、实心格 → 返回实心格顶端 y
  const y0 = Math.max(0, Math.floor((nearY - up) / 4) * 4);
  for (let y = y0; y <= nearY + down; y += 4) {
    if (!solidAt(T, x, y + 1) && solidAt(T, x, y + 5)) return y + 4;
    if (solidAt(T, x, y + 1)) { /* 已在实心里，找不到 */ if (y === y0) return null; }
  }
  return null;
}
export function groundAngleAt(T, x, footY) {
  const a = surfaceY(T, x - 8, footY, 32, 32), b = surfaceY(T, x + 8, footY, 32, 32);
  if (a == null || b == null) return 0;
  return Math.atan2(a - b, 16) * 180 / Math.PI;
}
export function muzzleAt(x, footY, groundAngle) {
  const g = groundAngle * Math.PI / 180;
  return { x: x - P.muzzleH * Math.sin(g), y: footY - P.muzzleH * Math.cos(g) };
}
