// Physics, terrain, and collision simulator for DanDanTang Golf

export const G = 300;
export const K_VEL = 6;
export const WIND_ACC = 11.25;
export const TICK_DT = 1 / 60;
export const SUBSTEPS = 16;
export const DT = TICK_DT / SUBSTEPS;

export function windFactor(y) {
  if (y >= 450) return 1;
  if (y <= 350) return 1.35;
  return 1 + 0.35 * (450 - y) / 100;
}

export function decodeTerrain(terrain) {
  const { width, height, cell, rle } = terrain;
  const grid = new Uint8Array(width * height);
  let idx = 0;
  for (let i = 0; i < rle.length; i++) {
    const val = rle[i][0];
    const count = rle[i][1];
    if (val) grid.fill(1, idx, idx + count);
    idx += count;
  }
  return { width, height, cell, grid };
}

export function cloneTerrain(ter) {
  return {
    width: ter.width,
    height: ter.height,
    cell: ter.cell,
    grid: new Uint8Array(ter.grid),
  };
}

export function isSolidPixel(ter, x, y) {
  if (y < 0) return false;
  const gx = Math.floor(x / ter.cell);
  const gy = Math.floor(y / ter.cell);
  if (gx < 0 || gx >= ter.width || gy >= ter.height) return false;
  return ter.grid[gy * ter.width + gx] === 1;
}

export function carveCircle(ter, cx, cy, r) {
  const { width, height, cell, grid } = ter;
  const r2 = r * r;
  const gx0 = Math.max(0, Math.floor((cx - r) / cell));
  const gx1 = Math.min(width - 1, Math.floor((cx + r) / cell));
  const gy0 = Math.max(0, Math.floor((cy - r) / cell));
  const gy1 = Math.min(height - 1, Math.floor((cy + r) / cell));
  for (let gy = gy0; gy <= gy1; gy++) {
    const py = (gy + 0.5) * cell;
    const dy = py - cy;
    const dy2 = dy * dy;
    const row = gy * width;
    for (let gx = gx0; gx <= gx1; gx++) {
      const px = (gx + 0.5) * cell;
      const dx = px - cx;
      if (dx * dx + dy2 <= r2) {
        grid[row + gx] = 0;
      }
    }
  }
}

export function isObjectActive(obj) {
  return !obj.removed && !obj.broken && !obj.stunned && !obj.inRecycle;
}

export function getObjectBox(obj, config) {
  if (obj.type === 'bug') {
    const r = obj.radius ?? config?.OBJECTS?.bug?.radius ?? 14;
    return { kind: 'circle', cx: obj.x, cy: obj.y - r, r, x0: obj.x - r, x1: obj.x + r, y0: obj.y - 2 * r, y1: obj.y };
  }
  const def = config?.OBJECTS?.[obj.type] ?? {};
  const w = obj.width ?? def.width ?? (obj.type === 'pushToken' ? 30 : 28);
  const h = obj.height ?? def.height ?? (obj.type === 'pushToken' ? 30 : 28);
  return {
    kind: 'rect',
    cx: obj.x,
    cy: obj.y - h / 2,
    w,
    h,
    x0: obj.x - w / 2,
    x1: obj.x + w / 2,
    y0: obj.y - h,
    y1: obj.y,
  };
}

export function hitTestObject(obj, x, y, config) {
  const b = getObjectBox(obj, config);
  if (b.kind === 'circle') {
    const dx = x - b.cx;
    const dy = y - b.cy;
    return dx * dx + dy * dy <= b.r * b.r;
  }
  return x >= b.x0 && x <= b.x1 && y >= b.y0 && y <= b.y1;
}

export function distPointToBox(x, y, box) {
  if (box.kind === 'circle') {
    return Math.max(0, Math.hypot(x - box.cx, y - box.cy) - box.r);
  }
  const dx = x < box.x0 ? box.x0 - x : x > box.x1 ? x - box.x1 : 0;
  const dy = y < box.y0 ? box.y0 - y : y > box.y1 ? y - box.y1 : 0;
  return Math.hypot(dx, dy);
}

export function distPointToCenter(x, y, box) {
  return Math.hypot(x - box.cx, y - box.cy);
}

export function hasLineOfSight(ter, x0, y0, x1, y1) {
  const dist = Math.hypot(x1 - x0, y1 - y0);
  const steps = Math.max(1, Math.ceil(dist / 3));
  for (let i = 1; i < steps; i++) {
    const t = i / steps;
    if (isSolidPixel(ter, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)) return false;
  }
  return true;
}

// Find surface Y at pixel x starting search from minFeetY (or 0) downward
export function findSurfaceAtX(ter, x, minFeetY = 0, bodyHeight = 60) {
  const gx = Math.floor(x / ter.cell);
  if (gx < 0 || gx >= ter.width) return null;
  const gyStart = Math.max(1, Math.floor(minFeetY / ter.cell));
  for (let gy = gyStart; gy < ter.height; gy++) {
    const solid = ter.grid[gy * ter.width + gx] === 1;
    const aboveEmpty = ter.grid[(gy - 1) * ter.width + gx] === 0;
    if (solid && aboveEmpty) {
      const ySurf = gy * ter.cell;
      if (ySurf < minFeetY - 0.01) continue;
      // Check headroom above surface
      const gyTop = Math.max(0, Math.floor((ySurf - bodyHeight) / ter.cell));
      let clear = true;
      for (let cy = gyTop; cy < gy; cy++) {
        if (ter.grid[cy * ter.width + gx] === 1) {
          clear = false;
          break;
        }
      }
      if (clear) return ySurf;
    }
  }
  return null;
}

// Find surface Y near a reference y (for slope fitting around a known feetY)
function findLocalSurfaceY(ter, x, refY, supportProbe) {
  const gx = Math.floor(x / ter.cell);
  if (gx < 0 || gx >= ter.width) return null;
  const gyMin = Math.max(1, Math.floor((refY - supportProbe) / ter.cell));
  const gyMax = Math.min(ter.height - 1, Math.ceil((refY + supportProbe) / ter.cell));
  let bestY = null;
  let bestDist = Infinity;
  for (let gy = gyMin; gy <= gyMax; gy++) {
    if (ter.grid[gy * ter.width + gx] === 1 && ter.grid[(gy - 1) * ter.width + gx] === 0) {
      const ySurf = gy * ter.cell;
      const d = Math.abs(ySurf - refY);
      if (d < bestDist) {
        bestDist = d;
        bestY = ySurf;
      }
    }
  }
  return bestY;
}

export function getStandPose(ter, x, tee, bodyCfg) {
  const minFeet = tee?.minFeet ?? 0;
  const hw = bodyCfg.halfWidth ?? 13;
  const bh = bodyCfg.height ?? 60;
  const span = bodyCfg.slopeFitSpan ?? 8;
  const probe = bodyCfg.supportProbe ?? 16;
  const maxTilt = bodyCfg.maxTilt ?? 45;

  const feetY = findSurfaceAtX(ter, x, minFeet, bh);
  if (feetY == null) return null;

  // Check body clearance across halfWidth
  const gx0 = Math.floor((x - hw + 1) / ter.cell);
  const gx1 = Math.floor((x + hw - 1) / ter.cell);
  const gyTop = Math.max(0, Math.floor((feetY - bh + 2) / ter.cell));
  const gyFeet = Math.floor((feetY - 2) / ter.cell);
  if (gx0 < 0 || gx1 >= ter.width) return null;
  for (let gy = gyTop; gy <= gyFeet; gy++) {
    const row = gy * ter.width;
    for (let gx = gx0; gx <= gx1; gx++) {
      if (ter.grid[row + gx] === 1) return null;
    }
  }

  const yL = findLocalSurfaceY(ter, x - span, feetY, probe);
  const yR = findLocalSurfaceY(ter, x + span, feetY, probe);
  if (yL == null || yR == null) return null;
  const groundAngle = Math.atan2(yL - yR, 2 * span) * 180 / Math.PI;
  if (Math.abs(groundAngle) > maxTilt) return null;
  return { x, feetY, groundAngle };
}

export function getMuzzle(x, feetY, groundAngle, muzzleHeight = 35) {
  const rad = groundAngle * Math.PI / 180;
  return {
    mx: x - Math.sin(rad) * muzzleHeight,
    my: feetY - Math.cos(rad) * muzzleHeight,
  };
}

export function simulateFlatProjectile(x0, y0, angleDeg, power, dir, wind, groundY = 780, dtScale = 1) {
  const rad = angleDeg * Math.PI / 180;
  const v0 = K_VEL * power;
  let x = x0;
  let y = y0;
  let vx = dir * v0 * Math.cos(rad);
  let vy = -v0 * Math.sin(rad);
  const dt = DT * dtScale;
  const maxSteps = Math.ceil(60000 / dtScale);
  for (let i = 0; i < maxSteps; i++) {
    if (y >= groundY && vy > 0) return { x, y, hit: 'ground' };
    x += vx * dt;
    y += vy * dt;
    vx += wind * WIND_ACC * windFactor(y) * dt;
    vy += G * dt;
    if (x < -50 || x > 1650 || y > 950) return { x, y, hit: 'oob' };
  }
  return { x, y, hit: 'timeout' };
}

export function simulateProjectile(ter, objects, config, x0, y0, actualAngleDeg, power, dir, wind, opts = {}) {
  const { ignoreObjects = false, dtScale = 1, maxSteps = 50000 } = opts;
  const W = config?.W ?? 1600;
  const H = config?.H ?? 900;
  const rad = actualAngleDeg * Math.PI / 180;
  const v0 = K_VEL * power;
  let x = x0;
  let y = y0;
  let vx = dir * v0 * Math.cos(rad);
  let vy = -v0 * Math.sin(rad);
  const dt = DT * dtScale;
  const limit = Math.ceil(maxSteps / dtScale);

  const activeObjs = ignoreObjects ? [] : objects.filter(isObjectActive);

  for (let i = 0; i < limit; i++) {
    if (x < 0 || x >= W || y >= H) {
      return { x, y, hit: 'oob', objectId: null, steps: i };
    }
    if (!ignoreObjects) {
      for (let j = 0; j < activeObjs.length; j++) {
        if (hitTestObject(activeObjs[j], x, y, config)) {
          return { x, y, hit: 'object', objectId: activeObjs[j].id, steps: i };
        }
      }
    }
    if (isSolidPixel(ter, x, y)) {
      return { x, y, hit: 'terrain', objectId: null, steps: i };
    }
    x += vx * dt;
    y += vy * dt;
    vx += wind * WIND_ACC * windFactor(y) * dt;
    vy += G * dt;
  }
  return { x, y, hit: 'oob', objectId: null, steps: limit };
}

// Simulate an entire attack (single, burst, spread, dig) with cratering & sequential object removal
export function simulateAttack(ter, objects, config, pose, kind, angle, power, dir, wind, dtScale = 1) {
  const atk = config.ATTACKS[kind];
  const { mx, my } = getMuzzle(pose.x, pose.feetY, pose.groundAngle, config.BODY.muzzleHeight);
  const baseActualAngle = angle + dir * pose.groundAngle;

  // Clone terrain and track object HP locally when multi-shell
  const isMulti = atk.offsets.length > 1 || atk.ticks.length > 1;
  const workTer = isMulti ? cloneTerrain(ter) : ter;
  const workObjs = isMulti ? objects.map(o => ({ ...o })) : objects;

  const impacts = [];
  if (kind === 'burst') {
    for (let s = 0; s < atk.ticks.length; s++) {
      const imp = simulateProjectile(workTer, workObjs, config, mx, my, baseActualAngle, power, dir, wind, { dtScale });
      impacts.push(imp);
      if (imp.hit !== 'oob') {
        applyExplosionToWorkState(workTer, workObjs, config, imp.x, imp.y, atk.radius, atk.damage, atk.damageRadius);
      }
    }
  } else if (kind === 'spread') {
    // All 3 launched at t=0; process in order of arrival (steps) so earlier hits carve before later hits
    const raw = atk.offsets.map((off, idx) => ({
      idx,
      off,
      imp: simulateProjectile(workTer, workObjs, config, mx, my, baseActualAngle + off, power, dir, wind, { dtScale }),
    }));
    // Check if an earlier impact changes a later impact's path
    raw.sort((a, b) => a.imp.steps - b.imp.steps);
    for (let s = 0; s < raw.length; s++) {
      const item = raw[s];
      const imp = s === 0
        ? item.imp
        : simulateProjectile(workTer, workObjs, config, mx, my, baseActualAngle + item.off, power, dir, wind, { dtScale });
      impacts.push(imp);
      if (imp.hit !== 'oob') {
        applyExplosionToWorkState(workTer, workObjs, config, imp.x, imp.y, atk.radius, atk.damage, atk.damageRadius);
      }
    }
  } else {
    const imp = simulateProjectile(workTer, workObjs, config, mx, my, baseActualAngle, power, dir, wind, { dtScale });
    impacts.push(imp);
  }

  return { impacts, mx, my };
}

function applyExplosionToWorkState(ter, objects, config, ix, iy, craterRadius, baseDamage, damageRadius) {
  carveCircle(ter, ix, iy, craterRadius);
  for (let i = 0; i < objects.length; i++) {
    const o = objects[i];
    if (!isObjectActive(o)) continue;
    const box = getObjectBox(o, config);
    const d = distPointToCenter(ix, iy, box);
    if (d < damageRadius) {
      // Only check LoS if not direct hit
      const direct = hitTestObject(o, ix, iy, config);
      if (direct || hasLineOfSight(ter, ix, iy, box.cx, box.cy)) {
        const dmg = direct ? baseDamage : baseDamage * (1 - d / damageRadius);
        if (o.type === 'token') {
          o.broken = true;
        } else if (o.type === 'chicken') {
          o.stunned = true;
        } else if (o.type === 'bigToken' || o.type === 'pushToken') {
          o.hp = (o.hp ?? 1) - dmg;
          if (o.hp <= 0) o.broken = true;
        }
      }
    }
  }
}
