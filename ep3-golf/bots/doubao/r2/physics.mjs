// 物理与地形库：试射拟合参数 + 弹道模拟 + 地形碰撞
// 拟合在“秒”单位下完成（16 发，平地 y=780）：
//   v0 = 0.108235 * power px/s，g = 0.097562 px/s²
//   风：ax(px/s²) = wind * c(h)，h=离地高度；h<=280 时 c=0.0036596（恒风，8 发误差≈0）
//       h>280 分段 ramp：c = 0.0036596 + 4.8e-6*(h-280)（仅 2 发高空数据，RMSE 3.4）
// 积分器按 tick 步进（每 tick = 1/60 秒），常量统一换算到 tick 单位：
//   v_tick = v_s / 60；a_tick = a_s / 3600。落点与秒单位解析解完全一致。
const SIXTY = 60;
export const K_V = 0.108235 / SIXTY;          // px/tick 每单位 power
export const GRAV = 0.097562 / (SIXTY * SIXTY); // px/tick²
export const WIND_C0 = 0.0036596 / (SIXTY * SIXTY); // px/tick² 每单位风力（低空）
export const WIND_H0 = 280;
export const WIND_C1 = 4.8e-6 / (SIXTY * SIXTY);    // 高空额外项
export const DT = 1; // 积分以 tick 为单位
export const MUZZLE_HEIGHT = 35; // BODY.muzzleHeight

export function windCoeff(h) {
  if (h <= WIND_H0) return WIND_C0;
  return WIND_C0 + WIND_C1 * (h - WIND_H0);
}

// 把 rle 展开成 Uint8Array（行优先，每行 GW 格）
export function decodeTerrain(terrain) {
  const { width: GW, height: GH, cell, rle } = terrain;
  const grid = new Uint8Array(GW * GH);
  let idx = 0;
  for (const [val, cnt] of rle) {
    if (val === 1) grid.fill(1, idx, idx + cnt);
    idx += cnt;
  }
  return { grid, GW, GH, cell };
}

export function solidAt(t, px, py) {
  const gx = Math.floor(px / t.cell);
  const gy = Math.floor(py / t.cell);
  if (gx < 0 || gy < 0 || gx >= t.GW || gy >= t.GH) return false;
  return t.grid[gy * t.GW + gx] === 1;
}

// 炮口世界坐标：脚底沿身体法线往上 muzzleHeight
// 脚底 = (actor.x, actor.y + footOffset=12)；身体法线相对竖直方向倾斜 groundAngle
export function muzzlePos(actor) {
  const footX = actor.x;
  const footY = actor.y + 12;
  const ga = (actor.groundAngle || 0) * Math.PI / 180;
  // 法线：身体沿坡面倾斜，炮口在脚底“往上”（身体方向）。
  // 地面倾角 groundAngle：坡面与水平的夹角；身体法线在世界系中为 (-sin ga, -cos ga)*h
  const mx = footX - Math.sin(ga) * MUZZLE_HEIGHT;
  const my = footY - Math.cos(ga) * MUZZLE_HEIGHT;
  return { x: mx, y: my };
}

// 模拟一发炮弹；返回落点 {x,y,hit} 与是否出界。
// t 为 null 时是无地形纯弹道（考卷/试射），用 groundY（默认 780）作为平地接敌面。
export function simulateShot({ t, muzzle, angleDeg, power, dir, groundAngle = 0, wind = 0,
  objects = [], groundY = 780, maxTicks = 16000 }) {
  const launch = (angleDeg + dir * groundAngle) * Math.PI / 180;
  const v = K_V * power;
  let x = muzzle.x, y = muzzle.y;
  let vx = dir * v * Math.cos(launch);
  let vy = -v * Math.sin(launch);
  // 风的高度参考：无地形用 groundY；有地形用炮口下方的支撑估计（近似 groundY 传入）
  const refGround = groundY;
  // 预计算物体碰撞盒，避免每 tick 重复取字段
  const boxes = objects.map(o => o.radius
    ? { id: o.id, r: o.radius + 2, cx: o.x, cy: o.y - o.radius, circle: true }
    : { id: o.id, x0: o.x - (o.width ?? 28) / 2, x1: o.x + (o.width ?? 28) / 2,
        y0: o.y - (o.height ?? 28), y1: o.y, circle: false });
  for (let i = 0; i < maxTicks; i++) {
    const h = refGround - y;
    vx += wind * windCoeff(h > 0 ? h : 0) * DT;
    vy += GRAV * DT;
    const nx = x + vx * DT, ny = y + vy * DT;
    // 物体碰撞
    for (let k = 0; k < boxes.length; k++) {
      const b = boxes[k];
      if (b.circle) {
        const ddx = nx - b.cx, ddy = ny - b.cy;
        if (ddx * ddx + ddy * ddy <= b.r * b.r) {
          return { x: nx, y: ny, hit: 'object', objectId: b.id, ticks: i };
        }
      } else if (nx >= b.x0 && nx <= b.x1 && ny >= b.y0 && ny <= b.y1) {
        return { x: nx, y: ny, hit: 'object', objectId: b.id, ticks: i };
      }
    }
    if (t) {
      // 地形碰撞：tick 位移通常 <1px，单点检测即可，跨格时补中点
      if (solidAt(t, nx, ny)) return { x: nx, y: ny, hit: 'terrain', ticks: i };
      const gx0 = Math.floor(x / t.cell), gx1 = Math.floor(nx / t.cell);
      const gy0 = Math.floor(y / t.cell), gy1 = Math.floor(ny / t.cell);
      if (gx0 !== gx1 || gy0 !== gy1) {
        const mx = (x + nx) / 2, my = (y + ny) / 2;
        if (solidAt(t, mx, my)) return { x: mx, y: my, hit: 'terrain', ticks: i };
      }
    } else if (ny >= groundY) {
      return { x: nx, y: groundY, hit: 'ground', ticks: i };
    }
    x = nx; y = ny;
    // 地图边界：出界或落出底部
    if (x < -40 || x > 1640 || y > 920) return { x, y, hit: 'out', ticks: i };
  }
  return { x, y, hit: 'timeout', ticks: maxTicks };
}

// 考卷/试射场：平地上的预测（脚底 y=780）
export function predictFlat({ x, angleDeg, power, dir, wind, groundY = 780 }) {
  const muzzle = { x, y: groundY - MUZZLE_HEIGHT };
  return simulateShot({ t: null, muzzle, angleDeg, power, dir, groundAngle: 0, wind, groundY });
}

// 给定目标点，网格搜索最佳 (angle, power, dir, moveX) 使落点最接近目标
// 返回 {angle,power,dir,moveX,impact,score}
export function aimAt({ obs, target, kind = 'single', preferDir = null, moveXs = null }) {
  const t = decodeTerrain(obs.terrain);
  const wind = obs.hole.wind || 0;
  const ga = obs.actor.groundAngle || 0;
  const tee = obs.hole.tee;
  const canMove = obs.hole.teeActive && tee;
  const positions = moveXs ?? (canMove
    ? [obs.actor.x, ...sampleStands(tee, t)]
    : [obs.actor.x]);
  const liveObjects = (obs.objects || []).filter(o => !o.removed && !o.broken && !o.stunned);
  let best = null;
  const dirs = preferDir ? [preferDir] : [1, -1];
  for (const moveX of positions) {
    const actor = { ...obs.actor, x: moveX };
    const muzzle = muzzlePos(actor);
    for (const dir of dirs) {
      for (let angle = 5; angle <= 85; angle += 2) {
        for (let power = 12; power <= 100; power += 4) {
          const imp = simulateShot({
            t, muzzle, angleDeg: angle, power, dir, groundAngle: ga, wind,
            objects: liveObjects, groundY: muzzle.y + 47,
          });
          const d = Math.hypot(imp.x - target.x, imp.y - target.y);
          // 轻微惩罚：低 power 更可控、出界重罚
          let score = d + power * 0.02;
          if (imp.hit === 'out') score += 5000;
          if (imp.hit === 'timeout') score += 3000;
          if (!best || score < best.score) {
            best = { angle, power, dir, moveX, impact: imp, score, stand: moveX };
          }
        }
      }
    }
  }
  return best;
}

// 发球台内可站稳的站位采样（脚下 4px 内有实心）
export function sampleStands(tee, t, step = 20) {
  const out = [];
  for (let x = tee.x0; x <= tee.x1; x += step) {
    if (findStandY(t, x, tee.minFeet) != null) out.push(x);
  }
  return out;
}

export function findStandY(t, x, minFeet) {
  // 从顶部向下找第一个实心格，脚底落在其顶部；minFeet 限制脚底高度（y 下限）
  for (let gy = 0; gy < t.GH; gy++) {
    const gx = Math.floor(x / t.cell);
    if (t.grid[gy * t.GW + gx] === 1) {
      const footY = gy * t.cell;
      if (minFeet != null && footY > minFeet) return null;
      return footY;
    }
  }
  return null;
}
