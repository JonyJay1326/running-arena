// bot/physics.mjs — 精确弹道、地形网格、站位倾角、爆炸伤害与击退物理模型

export const PHYS = {
  FPS: 60,
  SUBSTEPS: 30,
  DT: 1 / 1800,
  SPEED_PER_POWER: 15.5,
  GRAVITY: 2000,
  WIND_BASE: 75,
  WIND_Y_LOW: 460,
  WIND_Y_HIGH: 340,
  WIND_HIGH_BOOST: 0.35,
};

export function windMultiplier(y) {
  if (y >= PHYS.WIND_Y_LOW) return 1;
  if (y <= PHYS.WIND_Y_HIGH) return 1 + PHYS.WIND_HIGH_BOOST;
  const t = (PHYS.WIND_Y_LOW - y) / (PHYS.WIND_Y_LOW - PHYS.WIND_Y_HIGH);
  return 1 + PHYS.WIND_HIGH_BOOST * t * t * (3 - 2 * t);
}

export function decodeTerrain(terrain) {
  const { width, height, cell, rle } = terrain;
  const grid = new Uint8Array(width * height);
  let idx = 0;
  for (let i = 0; i < rle.length; i++) {
    const val = rle[i][0];
    const count = rle[i][1];
    if (val === 1) {
      grid.fill(1, idx, Math.min(grid.length, idx + count));
    }
    idx += count;
  }
  return { width, height, cell: cell || 4, grid };
}

export function cloneTerrain(t) {
  return { width: t.width, height: t.height, cell: t.cell, grid: new Uint8Array(t.grid) };
}

export function isSolidPixel(t, px, py) {
  const gx = Math.floor(px / t.cell);
  const gy = Math.floor(py / t.cell);
  if (gx < 0 || gx >= t.width || gy < 0) return false;
  if (gy >= t.height) return false;
  return t.grid[gy * t.width + gx] === 1;
}

export function carveCrater(t, cx, cy, radius) {
  const cell = t.cell;
  const r2 = radius * radius;
  const gx0 = Math.max(0, Math.floor((cx - radius) / cell));
  const gx1 = Math.min(t.width - 1, Math.floor((cx + radius) / cell));
  const gy0 = Math.max(0, Math.floor((cy - radius) / cell));
  const gy1 = Math.min(t.height - 1, Math.floor((cy + radius) / cell));
  let carved = 0;
  for (let gy = gy0; gy <= gy1; gy++) {
    const py = gy * cell + cell * 0.5;
    const dy = py - cy;
    const dy2 = dy * dy;
    const rowOff = gy * t.width;
    for (let gx = gx0; gx <= gx1; gx++) {
      if (t.grid[rowOff + gx] === 0) continue;
      const px = gx * cell + cell * 0.5;
      const dx = px - cx;
      if (dx * dx + dy2 <= r2) {
        t.grid[rowOff + gx] = 0;
        carved++;
      }
    }
  }
  return carved;
}

export function hasLineOfSight(t, x0, y0, x1, y1) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const dist = Math.hypot(dx, dy);
  if (dist < 2) return true;
  // Step every 2 pixels, skipping tiny 2px endpoint skin if object rests on ground
  const steps = Math.max(2, Math.ceil(dist / 2));
  for (let i = 1; i < steps; i++) {
    const frac = i / steps;
    const px = x0 + dx * frac;
    const py = y0 + dy * frac;
    if (isSolidPixel(t, px, py)) return false;
  }
  return true;
}

export function getMuzzlePosition(footX, footY, groundAngleDeg, config) {
  const mh = config?.BODY?.muzzleHeight ?? 35;
  const rad = (groundAngleDeg * Math.PI) / 180;
  return {
    x: footX - Math.sin(rad) * mh,
    y: footY - Math.cos(rad) * mh,
  };
}

export function getActorMuzzle(actor, config) {
  const footOffset = config?.BODY?.footOffset ?? 12;
  const footY = actor.y + footOffset;
  return getMuzzlePosition(actor.x, footY, actor.groundAngle || 0, config);
}

// Find surface y (top of solid cell) in column gx near or below minY
export function findColumnTopY(t, px, minY = 0, maxY = 900) {
  const cell = t.cell;
  const gx = Math.max(0, Math.min(t.width - 1, Math.floor(px / cell)));
  const gyStart = Math.max(0, Math.floor(minY / cell));
  const gyEnd = Math.min(t.height - 1, Math.floor(maxY / cell));
  // Ensure at least 1 empty cell above the surface
  for (let gy = gyStart; gy <= gyEnd; gy++) {
    if (t.grid[gy * t.width + gx] === 1) {
      if (gy === 0 || t.grid[(gy - 1) * t.width + gx] === 0) {
        return gy * cell;
      }
    }
  }
  return null;
}

// Check headroom for body standing with foot at (px, footY)
export function hasHeadroom(t, px, footY, config) {
  const hw = config?.BODY?.halfWidth ?? 13;
  const bodyH = config?.BODY?.height ?? 60;
  const stepMax = config?.BODY?.STEP_MAX ?? config?.STEP_MAX ?? 16;
  // Check central & side clearance above step tolerance
  const yTop = Math.max(0, footY - bodyH + 4);
  const yBot = footY - stepMax - 1;
  for (let x = px - hw + 3; x <= px + hw - 3; x += 4) {
    for (let y = yTop; y <= yBot; y += 4) {
      if (isSolidPixel(t, x, y)) return false;
    }
  }
  return true;
}

export function estimateGroundAngle(t, px, footY, config) {
  const maxTilt = config?.BODY?.maxTilt ?? 45;
  const span = config?.BODY?.supportProbe ?? 16; // 16px left & right = 32px span (verified exact 14.0362° and 7.1250° in Lab #1)
  const probe = span + 20;
  const yL = findColumnTopY(t, px - span, Math.max(0, footY - probe), Math.min(t.height * t.cell - 1, footY + probe));
  const yR = findColumnTopY(t, px + span, Math.max(0, footY - probe), Math.min(t.height * t.cell - 1, footY + probe));
  if (yL == null || yR == null || Math.abs(yL - yR) > probe * 1.5) return 0;
  const deg = (Math.atan2(yL - yR, 2 * span) * 180) / Math.PI;
  return Math.max(-maxTilt, Math.min(maxTilt, deg));
}

export function estimateFootY(t, px, rawFootY) {
  const yL = findColumnTopY(t, px - 8, Math.max(0, rawFootY - 16), Math.min(t.height * t.cell - 1, rawFootY + 16));
  const yR = findColumnTopY(t, px + 8, Math.max(0, rawFootY - 16), Math.min(t.height * t.cell - 1, rawFootY + 16));
  if (yL != null && yR != null && Math.abs(yL - yR) <= 12) {
    return 0.5 * (yL + yR);
  }
  return rawFootY;
}

export function getStandCandidates(t, hole, actor, config, stepPx = 12) {
  const footOffset = config?.BODY?.footOffset ?? 12;
  const curFootY = actor.y + footOffset;

  if (!hole.teeActive || !hole.tee) {
    return [{
      x: actor.x,
      footY: curFootY,
      actorY: actor.y,
      groundAngle: actor.groundAngle || 0,
      muzzle: getMuzzlePosition(actor.x, curFootY, actor.groundAngle || 0, config),
      isCurrent: true,
    }];
  }

  const x0 = Math.ceil(Math.max(20, hole.tee.x0));
  const x1 = Math.floor(Math.min((config?.W ?? 1600) - 20, hole.tee.x1));
  const minFeet = hole.tee.minFeet ?? 0;
  const stepMax = config?.STEP_MAX ?? 16;

  // Walk left and right from actor.x in 4px increments to guarantee connected/reachable surface,
  // and also check general valid surfaces >= minFeet inside tee.
  const reachable = new Map();
  reachable.set(Math.round(actor.x), {
    x: Math.round(actor.x),
    footY: curFootY,
    actorY: actor.y,
    groundAngle: actor.groundAngle || 0,
    isCurrent: true,
  });

  // Walk right
  let prevY = curFootY;
  for (let x = Math.round(actor.x) + 4; x <= x1; x += 4) {
    const rawFy = findColumnTopY(t, x, Math.max(minFeet - 4, prevY - stepMax), prevY + stepMax + 8);
    if (rawFy == null || rawFy < minFeet || Math.abs(rawFy - prevY) > stepMax || !hasHeadroom(t, x, rawFy, config)) break;
    const fy = estimateFootY(t, x, rawFy);
    reachable.set(x, {
      x,
      footY: fy,
      actorY: fy - footOffset,
      groundAngle: estimateGroundAngle(t, x, rawFy, config),
      isCurrent: false,
    });
    prevY = rawFy;
  }

  // Walk left
  prevY = curFootY;
  for (let x = Math.round(actor.x) - 4; x >= x0; x -= 4) {
    const rawFy = findColumnTopY(t, x, Math.max(minFeet - 4, prevY - stepMax), prevY + stepMax + 8);
    if (rawFy == null || rawFy < minFeet || Math.abs(rawFy - prevY) > stepMax || !hasHeadroom(t, x, rawFy, config)) break;
    const fy = estimateFootY(t, x, rawFy);
    reachable.set(x, {
      x,
      footY: fy,
      actorY: fy - footOffset,
      groundAngle: estimateGroundAngle(t, x, rawFy, config),
      isCurrent: false,
    });
    prevY = rawFy;
  }

  // Subsample at stepPx while always keeping actor.x and endpoints
  const keys = [...reachable.keys()].sort((a, b) => a - b);
  const selected = [];
  for (let i = 0; i < keys.length; i++) {
    const k = keys[i];
    const item = reachable.get(k);
    if (
      item.isCurrent ||
      i === 0 ||
      i === keys.length - 1 ||
      selected.length === 0 ||
      Math.abs(k - selected[selected.length - 1].x) >= stepPx
    ) {
      item.muzzle = getMuzzlePosition(item.x, item.footY, item.groundAngle, config);
      selected.push(item);
    }
  }
  return selected;
}

export function isObjectActive(o) {
  return !o.removed && !o.broken && !o.stunned && !o.inRecycle;
}

export function getObjectShape(o, config) {
  if (o.type === 'bug' || o.radius != null) {
    const r = o.radius ?? config?.OBJECTS?.bug?.radius ?? 14;
    return { isCircle: true, cx: o.x, cy: o.y - r, r, x0: o.x - r, y0: o.y - 2 * r, x1: o.x + r, y1: o.y };
  }
  const def = config?.OBJECTS?.[o.type] || {};
  const w = o.width ?? def.width ?? 30;
  const h = o.height ?? def.height ?? 30;
  return {
    isCircle: false,
    cx: o.x,
    cy: o.y - h * 0.5,
    w,
    h,
    x0: o.x - w * 0.5,
    y0: o.y - h,
    x1: o.x + w * 0.5,
    y1: o.y,
  };
}

export function pointHitsShape(px, py, sh) {
  if (px < sh.x0 || px > sh.x1 || py < sh.y0 || py > sh.y1) return false;
  if (sh.isCircle) {
    const dx = px - sh.cx;
    const dy = py - sh.cy;
    return dx * dx + dy * dy <= sh.r * sh.r;
  }
  return true;
}

export function distanceToShape(px, py, sh) {
  if (sh.isCircle) {
    return Math.max(0, Math.hypot(px - sh.cx, py - sh.cy) - sh.r);
  }
  const cx = Math.max(sh.x0, Math.min(sh.x1, px));
  const cy = Math.max(sh.y0, Math.min(sh.y1, py));
  return Math.hypot(px - cx, py - cy);
}

export function distanceToCenter(px, py, sh) {
  return Math.hypot(px - sh.cx, py - sh.cy);
}

// Exact projectile simulation (substepFactor = 1 for exact dt=1/1800; >1 for fast coarse search)
export function simulateProjectile({
  startX,
  startY,
  actualAngleDeg,
  power,
  dir,
  wind,
  terrain,
  objects,
  config,
  ignoreObjects = false,
  substepFactor = 1,
  flatGroundY = 780,
}) {
  const W = config?.W ?? 1600;
  const H = config?.H ?? 900;
  const dt = PHYS.DT * substepFactor;
  const rad = (actualAngleDeg * Math.PI) / 180;
  const v0 = PHYS.SPEED_PER_POWER * power;
  let x = startX;
  let y = startY;
  let vx = dir * v0 * Math.cos(rad);
  let vy = -v0 * Math.sin(rad);

  const activeShapes = [];
  if (!ignoreObjects && objects && objects.length > 0) {
    for (let i = 0; i < objects.length; i++) {
      const o = objects[i];
      if (isObjectActive(o)) {
        activeShapes.push({ obj: o, sh: getObjectShape(o, config) });
      }
    }
  }

  const maxSteps = Math.ceil(18000 / substepFactor);
  let minY = y;
  const cell = terrain ? terrain.cell : 4;
  const gw = terrain ? terrain.width : 400;
  const gh = terrain ? terrain.height : 225;
  const grid = terrain ? terrain.grid : null;

  for (let step = 0; step < maxSteps; step++) {
    const wMul = wind === 0 ? 1 : windMultiplier(y);
    vx += PHYS.WIND_BASE * wind * wMul * dt;
    vy += PHYS.GRAVITY * dt;
    x += vx * dt;
    y += vy * dt;
    if (y < minY) minY = y;

    if (x < 0 || x >= W || y >= H) {
      return { x, y, hit: 'out', objectId: null, steps: step + 1, minY };
    }

    if (grid && y >= 0) {
      const gx = (x / cell) | 0;
      const gy = (y / cell) | 0;
      if (gx >= 0 && gx < gw && gy >= 0 && gy < gh && grid[gy * gw + gx] === 1) {
        return { x, y, hit: 'ground', objectId: null, steps: step + 1, minY };
      }
    } else if (!grid && y >= flatGroundY) {
      return { x, y, hit: 'ground', objectId: null, steps: step + 1, minY };
    }

    for (let i = 0; i < activeShapes.length; i++) {
      const item = activeShapes[i];
      if (pointHitsShape(x, y, item.sh)) {
        return { x, y, hit: 'object', objectId: item.obj.id, steps: step + 1, minY };
      }
    }
  }
  return { x, y, hit: 'timeout', objectId: null, steps: maxSteps, minY };
}

// Predict flat-ground exam question (ground = 780, footOffset = 12, muzzleHeight = 35)
export function predictExamQuestion(q, ground, config) {
  const mh = config?.BODY?.muzzleHeight ?? 35;
  const gY = ground ?? 780;
  const startX = q.x;
  const startY = gY - mh;
  const res = simulateProjectile({
    startX,
    startY,
    actualAngleDeg: q.angle,
    power: q.power,
    dir: q.dir,
    wind: q.wind,
    terrain: null,
    objects: null,
    config,
    ignoreObjects: true,
    substepFactor: 1,
    flatGroundY: gY,
  });
  return {
    id: q.id,
    x: Math.round(res.x * 100) / 100,
    y: Math.round(res.y * 100) / 100,
  };
}

// Check if an object has solid ground support anywhere across its full width, or if it falls out of the map
export function checkSupportBelow(t, x, startY, halfW = 10) {
  const maxY = t.height * t.cell;
  const w1 = halfW * 0.48;
  const w2 = Math.max(2, halfW * 0.92 - 1);
  for (let y = Math.max(0, Math.floor(startY)); y < maxY; y += 4) {
    if (
      isSolidPixel(t, x, y) ||
      isSolidPixel(t, x - w1, y) ||
      isSolidPixel(t, x + w1, y) ||
      isSolidPixel(t, x - w2, y) ||
      isSolidPixel(t, x + w2, y)
    ) {
      return y;
    }
  }
  return null; // falls out of map!
}

// Measure total vertical solid thickness and layer count supporting an object across its full width down to map bottom
export function measureSupportLayersBelow(t, x, startY, halfW = 14) {
  const maxY = t.height * t.cell;
  const w2 = Math.max(2, halfW * 0.92 - 1);
  const xs = [x, x - halfW * 0.48, x + halfW * 0.48, x - w2, x + w2];
  let maxSolidPx = 0;
  let maxLayers = 0;
  let firstTopY = null;

  for (const sx of xs) {
    let solidPx = 0;
    let layers = 0;
    let inSolid = false;
    for (let y = Math.max(0, Math.floor(startY)); y < maxY; y += 4) {
      const s = isSolidPixel(t, sx, y);
      if (s) {
        solidPx += 4;
        if (!inSolid) {
          inSolid = true;
          layers++;
          if (firstTopY == null || y < firstTopY) firstTopY = y;
        }
      } else {
        inSolid = false;
      }
    }
    if (solidPx > maxSolidPx) maxSolidPx = solidPx;
    if (layers > maxLayers) maxLayers = layers;
  }

  // Each dig (r=60) clears up to ~85px of contiguous vertical rock or 1 separated thin layer
  const estDigStrokes = maxSolidPx === 0 ? 0 : Math.max(maxLayers, Math.ceil(maxSolidPx / 72));
  return { solidPx: maxSolidPx, layers: maxLayers, firstTopY, estDigStrokes };
}

// Simulate full ballistic flight + bounce + slope-energy ground-friction slide of Bug or pushToken
export function simulateBodyImpulse(t, startX, startY, radiusOrHalfW, vx0, vy0, slideK, bodyG, config) {
  const W = config?.W ?? 1600;
  const H = config?.H ?? 900;
  const r = radiusOrHalfW;
  let cx = startX;
  let cy = startY - r;
  let vx = vx0;
  let vy = vy0;
  const dt = 1 / 60;
  const damp = Math.exp(-dt / Math.max(0.1, slideK));
  const initSup = findColumnTopY(t, cx, Math.max(0, startY - 6), Math.min(H - 1, startY + 6));
  let grounded = vy0 >= -5 && initSup != null;
  if (grounded) {
    cy = initSup - r;
    vy = 0;
  }
  let bounces = 0;

  for (let tick = 0; tick < 260; tick++) {
    if (!grounded) {
      vy += bodyG * dt;
      const nx = cx + vx * dt;
      const ny = cy + vy * dt;
      if (nx < r * 0.6 || nx >= W - r * 0.6 || ny >= H) {
        return { x: nx, y: H + 10, fellOut: true, bounces };
      }
      const dirX = vx >= 0 ? 1 : -1;
      if (Math.abs(vx) > 5 && isSolidPixel(t, nx + dirX * r * 0.85, cy)) {
        bounces++;
        if (!isSolidPixel(t, nx + dirX * r * 0.85, cy - 10)) {
          cx = nx;
          cy -= 5;
          vy = Math.min(vy, -40);
          vx *= 0.85;
        } else {
          vx *= -0.3;
        }
      } else {
        cx = nx;
      }
      if (vy < 0 && isSolidPixel(t, cx, ny - r * 0.85)) {
        vy = -vy * 0.25;
        bounces++;
      } else {
        cy = ny;
      }
      if (vy >= 0) {
        // Start search from top of body (cy - r, guaranteed in air) so fast fall never phases into solid rock!
        const supY = findColumnTopY(t, cx, Math.max(0, cy - r), Math.min(H - 1, cy + r + 14));
        if (supY != null && cy + r >= supY) {
          cy = supY - r;
          if (vy > 130) {
            // Bouncy ball restitution on hard landing!
            vy = -vy * 0.35;
            vx *= 0.88;
            bounces++;
          } else {
            vy = 0;
            grounded = true;
          }
        }
      }
    } else {
      vx *= damp;
      if (Math.abs(vx) < 6) break;
      const stepDx = vx * dt;
      const nx = cx + stepDx;
      if (nx < r * 0.6 || nx >= W - r * 0.6) {
        return { x: nx, y: H + 10, fellOut: true, bounces };
      }
      const dirX = vx >= 0 ? 1 : -1;
      // Check vertical wall ahead (> 6px abrupt step blocks ground sliding!)
      if (isSolidPixel(t, nx + dirX * r * 0.75, cy + r - 7)) {
        bounces++;
        vx = 0;
        break;
      }
      const nearGround = findColumnTopY(t, nx, Math.max(0, cy - r), Math.min(H - 1, cy + r + 12));
      if (nearGround != null) {
        const dy = (nearGround - r) - cy; // dy < 0 means climbing uphill; dy > 0 means rolling downhill
        if (dy < 0) {
          // Potential energy cost to climb uphill: v^2_new = v^2 - 2 * G * (-dy)
          const v2 = vx * vx - 2.2 * bodyG * (-dy);
          if (v2 <= 36) {
            vx = 0;
            break;
          }
          vx = dirX * Math.sqrt(v2);
        } else if (dy > 0) {
          // Rolling downhill gains speed!
          const v2 = vx * vx + 1.3 * bodyG * dy;
          vx = dirX * Math.sqrt(v2);
        }
        cx = nx;
        cy = nearGround - r;
      } else {
        cx = nx;
        grounded = false;
      }
    }
  }

  const finalSup = findColumnTopY(t, cx, Math.max(0, cy - r), H - 1) ?? checkSupportBelow(t, cx, Math.max(0, cy), r);
  if (finalSup == null) {
    return { x: cx, y: H + 10, fellOut: true, bounces };
  }
  return { x: cx, y: finalSup, fellOut: false, bounces };
}

// Estimate splash damage & knockback velocity from an explosion at (ix, iy)
export function computeExplosionEffect(ix, iy, attackSpec, obj, preTerrain, postTerrain, config, cal) {
  const sh = getObjectShape(obj, config);
  const dmgRadius = attackSpec.damageRadius ?? 30;
  const distShape = distanceToShape(ix, iy, sh);
  const distCenter = distanceToCenter(ix, iy, sh);
  const useShapeDist = cal?.useShapeDist ?? true;
  const effDist = useShapeDist ? distShape : distCenter;

  const nearX = sh.isCircle ? sh.cx : Math.max(sh.x0, Math.min(sh.x1, ix));
  const nearY = sh.isCircle ? sh.cy : Math.max(sh.y0, Math.min(sh.y1, iy));
  const losOk =
    effDist < 2 ||
    hasLineOfSight(preTerrain, ix, iy, sh.cx, sh.cy) ||
    hasLineOfSight(preTerrain, ix, iy, nearX, nearY);

  let damage = 0;
  if (obj.type === 'chicken') {
    // Unconditional 35px blast buffer for chickens (craters blow through thin rock lips and AABB corners protrude!)
    const safeRad = dmgRadius + 5;
    if (effDist < safeRad) {
      damage = attackSpec.damage * (1 - effDist / safeRad);
    }
  } else if (losOk && effDist < dmgRadius) {
    damage = attackSpec.damage * (1 - effDist / dmgRadius);
  }

  // Ballistic + slide knockback velocity for bug and pushToken
  // Empirically verified cutoff: R_center = 52 for single/burst/spread (64 for dig), Vmax = 800, slideK = 0.65
  let vx0 = 0;
  let vy0 = 0;
  const baseKnockR = cal?.knockRadius ?? 52;
  const knockRadius = attackSpec.kind === 'dig' ? baseKnockR + 12 : baseKnockR;
  if (losOk && (obj.type === 'bug' || obj.type === 'pushToken') && distCenter < knockRadius) {
    const dx = sh.cx - ix;
    const dy = sh.cy - iy;
    const d = Math.max(4, Math.hypot(dx, dy));
    const falloff = Math.max(0, 1 - d / knockRadius);
    const vMax = obj.type === 'bug' ? (cal?.bugVmax ?? 800) : (cal?.pushVmax ?? 500);
    const ux = Math.abs(dx) < 1.8 ? 0 : dx / d;
    const uy = dy / d;
    vx0 = vMax * falloff * ux;
    vy0 = vMax * falloff * uy;
  }

  return { damage, vx0, vy0, losOk, distShape, distCenter };
}
