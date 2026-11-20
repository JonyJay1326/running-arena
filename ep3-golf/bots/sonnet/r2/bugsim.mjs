// Bug 被爆炸推走的近似模型（由 lab exp1/2/3 拟合：平地推距表 + 平台落下一发）
import { solidAt } from './phys.mjs';
const G = 1500, AF = 800, UP = 0.3, DT = 0.004;
// 平地推距表：d(爆炸点到Bug中心) -> L
export const PUSH_TAB = [[24, 520], [33.1, 346], [35.9, 287], [39.6, 228], [44, 166], [48.1, 124], [52, 88], [54, 30], [56, 0]];
function groundTopAt(T, x, y) { // 从 y-6 向下找到第一个实心像素
  for (let yy = Math.max(0, y - 6); yy < 905; yy += 2) if (solidAt(T, x, yy)) return yy;
  return 905;
}
// 用平地模型把 L 反推成初速表
function flatSim(v, ux, uy) {
  let vx = v * ux, vy = v * uy - UP * v; if (vy > 0) vy = 0;
  let x = 0, h = 0, air = vy < 0;
  for (let i = 0; i < 200000; i++) {
    if (air) { vy += G * DT; h -= vy * DT; x += vx * DT; if (h <= 0) { h = 0; air = false; } }
    else { const sp = Math.abs(vx), nv = Math.max(0, sp - AF * DT); x += Math.sign(vx) * (sp + nv) / 2 * DT; vx = Math.sign(vx) * nv; if (nv < 0.001) break; }
  }
  return x;
}
const SPEED_TAB = PUSH_TAB.map(([d, L]) => {
  const dx = Math.sqrt(Math.max(1, d * d - 196)), ux = dx / d, uy = -14 / d; // 爆炸点在底部，bug中心在其上方
  let lo = 0, hi = 5000;
  for (let k = 0; k < 40; k++) { const m = (lo + hi) / 2; if (flatSim(m, ux, -uy * 0 + (-14 / d) * -1 * -1) < L) lo = m; else hi = m; }
  return [d, (lo + hi) / 2];
});
export function bugSpeed(d) {
  if (d >= 56) return 0;
  if (d <= SPEED_TAB[0][0]) return SPEED_TAB[0][1] * (1 + 0.03 * (SPEED_TAB[0][0] - Math.max(14, d)));
  for (let i = 1; i < SPEED_TAB.length; i++) if (d <= SPEED_TAB[i][0]) {
    const [d0, s0] = SPEED_TAB[i - 1], [d1, s1] = SPEED_TAB[i];
    return s0 + (s1 - s0) * (d - d0) / (d1 - d0);
  }
  return 0;
}
// 返回 {x,y(底部),out}
export function bugAfter(T, bug, ex, ey, scale = 1) {
  const r = bug.radius ?? 14;
  const cx = bug.x, cy = bug.y - r;
  const d = Math.hypot(cx - ex, cy - ey);
  if (d >= 56) return { x: bug.x, y: bug.y, moved: false };
  // 第二轮：贴脸/直击（d<24，表外）第一轮09号洞#3 实测只推 10px（爆点在 Bug 正上方，向下推进地里），表外推外插会高估上百px→按 (d/24)^3 压低
  const v = bugSpeed(d) * scale * (d < 24 ? Math.pow(Math.max(d, 8) / 24, 3) : 1);
  const ux = (cx - ex) / Math.max(d, 1e-6), uy = (cy - ey) / Math.max(d, 1e-6); // y 向下为正
  let vx = v * ux, vy = v * uy - UP * v;
  let x = bug.x, y = bug.y;
  const grounded0 = solidAt(T, x, y + 3);
  let air = true;
  if (grounded0 && vy > 0) { vy = 0; }
  if (grounded0 && vy === 0) air = false;
  for (let i = 0; i < 400000; i++) {
    if (air) {
      vy += G * DT;
      const nx = x + vx * DT, ny = y + vy * DT;
      // 墙壁
      if (solidAt(T, nx + Math.sign(vx) * r, ny - r)) { vx = 0; }
      else x = nx;
      y = ny;
      if (x < 0 || x > 1600 || y > 900) return { x, y, out: true };
      if (solidAt(T, x, y) && vy >= 0) { // 落地：回到表面
        while (solidAt(T, x, y - 1) && y > 0) y -= 1;
        air = false; vy = 0;
      }
    } else {
      const sp = Math.abs(vx), nv = Math.max(0, sp - AF * DT);
      const step = Math.sign(vx) * (sp + nv) / 2 * DT; vx = Math.sign(vx) * nv;
      const nx = x + step;
      if (nv < 0.001 && Math.abs(step) < 1e-9) break;
      if (nx < 0 || nx > 1600) return { x: nx, y, out: true };
      if (solidAt(T, nx + Math.sign(step) * r, y - r)) { vx = 0; break; }
      // 地面高度
      let ny = y;
      if (!solidAt(T, nx, y + 1)) { // 脚下悬空 → 掉落
        x = nx; air = true; vy = 0; continue;
      }
      // 向上爬小台阶
      let up = 0; while (solidAt(T, nx, ny - 1) && up < 12) { ny -= 1; up++; }
      if (up >= 12) { vx = 0; break; }
      x = nx; y = ny;
      if (nv < 0.001) break;
    }
  }
  return { x, y, out: false, moved: true };
}
