import { CONFIG as C } from './config.mjs';
import { solidAt, visible } from './geometry.mjs';

export function center(o) { return { x: o.x, y: o.y - (o.type === 'bug' ? o.radius : o.height / 2) }; }
export function objectDistance(o, x, y) {
  if (o.type === 'bug') { const c = center(o); return Math.max(0, Math.hypot(x - c.x, y - c.y) - o.radius); }
  return Math.hypot(Math.max(o.x - o.width / 2 - x, 0, x - o.x - o.width / 2), Math.max(o.y - o.height - y, 0, y - o.y));
}
export function visibleObjectDistance(grid, o, x, y, radius) {
  const points = [];
  if (o.type === 'bug') {
    const c = center(o), a = Math.atan2(y - c.y, x - c.x);
    points.push([c.x + Math.cos(a) * o.radius, c.y + Math.sin(a) * o.radius]);
    for (let i = 0; i < 48; i++) points.push([c.x + Math.cos(i * Math.PI / 24) * o.radius, c.y + Math.sin(i * Math.PI / 24) * o.radius]);
  } else {
    points.push([Math.max(o.x - o.width / 2, Math.min(o.x + o.width / 2, x)), Math.max(o.y - o.height, Math.min(o.y, y))]);
    for (let dx = -o.width / 2; dx <= o.width / 2; dx += 2) points.push([o.x + dx, o.y], [o.x + dx, o.y - o.height]);
    for (let dy = 0; dy <= o.height; dy += 2) points.push([o.x - o.width / 2, o.y - dy], [o.x + o.width / 2, o.y - dy]);
  }
  let best = Infinity;
  for (const [px, py] of points) { const d = Math.hypot(px - x, py - y); if (d < radius && d < best && visible(grid, x, y, px, py)) best = d; }
  return best;
}
// 在脚底附近寻找真正暴露的格子顶面。圆形用圆边拟合，避免把台阶当作平滑高度图。
export function surfaceBelow(grid, o, maxRise = 1, maxDrop = 2) {
  const half = o.type === 'bug' ? o.radius : o.width / 2;
  let best = Infinity;
  for (let gx = Math.max(0, Math.floor((o.x - half + 1e-6) / C.CELL)); gx <= Math.min(C.GW - 1, Math.floor((o.x + half - 1e-6) / C.CELL)); gx++) {
    const dx = Math.max(gx * C.CELL - o.x, 0, o.x - (gx + 1) * C.CELL);
    const inset = o.type === 'bug' ? o.radius - Math.sqrt(Math.max(0, o.radius ** 2 - dx ** 2)) : 0;
    for (let gy = Math.max(0, Math.floor((o.y - maxRise - inset) / C.CELL)); gy <= Math.min(C.GH - 1, Math.ceil((o.y + maxDrop) / C.CELL)); gy++) {
      if (!grid[gy * C.GW + gx] || gy > 0 && grid[(gy - 1) * C.GW + gx]) continue;
      const feet = gy * C.CELL + inset;
      if (feet >= o.y - maxRise - 1e-6 && feet <= o.y + maxDrop + 1e-6) best = Math.min(best, feet);
    }
  }
  return Number.isFinite(best) ? best : null;
}
// 已嵌入地形的物体：沿 8 个方向以 0.1 像素步长（最远 maxDist）寻找最近的不相交位置并移过去；找不到返回 false 且不改动。
// 只在下落被地形拒绝且移动前就已相交时使用，避免旧的嵌入让物体每个子步回退、悬停到结算上限。
const EJECT_DIRS = [[1, 0], [-1, 0], [0, -1], [1, -1], [-1, -1], [0, 1], [1, 1], [-1, 1]].map(([x, y]) => { const n = Math.hypot(x, y); return [x / n, y / n]; });
export function ejectFromTerrain(grid, o, maxDist = 4) {
  const x0 = o.x, y0 = o.y;
  for (let step = 1; step <= Math.round(maxDist * 10); step++) {
    for (const [ux, uy] of EJECT_DIRS) {
      o.x = x0 + ux * step / 10; o.y = y0 + uy * step / 10;
      if (!intersectsTerrain(grid, o)) return true;
    }
  }
  o.x = x0; o.y = y0; return false;
}
export function intersectsTerrain(grid, o) {
  const half = o.type === 'bug' ? o.radius : o.width / 2, height = o.type === 'bug' ? 2 * o.radius : o.height;
  for (let gy = Math.max(0, Math.floor((o.y - height) / C.CELL)); gy <= Math.min(C.GH - 1, Math.floor((o.y - 1e-6) / C.CELL)); gy++) {
    for (let gx = Math.max(0, Math.floor((o.x - half) / C.CELL)); gx <= Math.min(C.GW - 1, Math.floor((o.x + half - 1e-6) / C.CELL)); gx++) {
      if (!solidAt(grid, gx * C.CELL, gy * C.CELL)) continue;
      if (o.type !== 'bug') return true;
      const c = center(o), dx = Math.max(gx * C.CELL - c.x, 0, c.x - (gx + 1) * C.CELL), dy = Math.max(gy * C.CELL - c.y, 0, c.y - (gy + 1) * C.CELL);
      if (dx * dx + dy * dy < o.radius ** 2 - 1e-6) return true;
    }
  }
  return false;
}
