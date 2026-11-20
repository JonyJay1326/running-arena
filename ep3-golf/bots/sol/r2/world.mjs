import { clamp, DEG, setPosition, shape } from './geometry.mjs';

export const token = o => o.type === 'token' || o.type === 'bigToken' || o.type === 'pushToken';
export const alive = o => !o.dead && !o.removed && !o.broken && !o.stunned && !o.inRecycle;
export const wanted = (o, goal) => alive(o) && ((token(o) && goal !== 'bug') || (o.type === 'bug' && goal !== 'tokens'));

export function objectList(obs, config) {
  return obs.objects.map(o => shape(o, config.OBJECTS?.[o.type]));
}

export function inside(o, box) {
  return !!box && o.x >= box.x0 && o.x <= box.x1 && o.cy >= box.y0 && o.cy <= box.y1;
}

function fitGround(terrain, x, feet, body) {
  const span = body.supportProbe ?? 16;
  const left = terrain.surface(x - span, feet - 29, feet + 28);
  const right = terrain.surface(x + span, feet - 29, feet + 28);
  if (!Number.isFinite(left) || !Number.isFinite(right)) return { groundAngle: 0, feet };
  const slope = clamp((left - right) / (span * 2), -1, 1);
  const groundAngle = clamp(Math.atan(slope) / DEG, -body.maxTilt, body.maxTilt);
  if (Math.abs(slope) < 0.01) return { groundAngle, feet };
  const extent = Math.floor(body.halfWidth / terrain.cell) * terrain.cell;
  let fittedFeet = feet;
  for (let offset = -extent; offset <= extent; offset += terrain.cell) {
    const y = terrain.surface(x + offset, feet - 26, feet + 30);
    fittedFeet = Math.min(fittedFeet, y + slope * offset);
  }
  return { groundAngle, feet: fittedFeet };
}

export function station(terrain, x, config, minFeet = 0) {
  const body = config.BODY;
  const half = Math.floor(body.halfWidth * 0.7);
  let feet = terrain.surface(x, minFeet + 0.05);
  while (Number.isFinite(feet)) {
    let free = feet >= minFeet && !terrain.solid(x, feet - 1);
    for (let y = feet - body.height; free && y < feet - 9; y += 5) {
      if (terrain.solid(x, y) || terrain.solid(x - half, y) || terrain.solid(x + half, y)) free = false;
    }
    if (free) {
      const fitted = fitGround(terrain, x, feet, body);
      if (fitted.feet >= minFeet - 0.5) {
        return { x, y: fitted.feet - body.footOffset, groundAngle: fitted.groundAngle, grounded: true };
      }
    }
    do {
      feet += terrain.cell;
    } while (feet < terrain.h && terrain.solid(x, feet + 0.1));
    feet = terrain.surface(x, feet);
  }
  return null;
}

export function stationSupports(terrain, actor, config) {
  const { halfWidth, footOffset } = config.BODY;
  const feet = actor.y + footOffset;
  const slope = Math.tan(actor.groundAngle * DEG);
  for (const offset of [0, -halfWidth, halfWidth, -0.65 * halfWidth, 0.65 * halfWidth]) {
    const x = actor.x + offset;
    const contact = feet - slope * offset;
    if (terrain.solid(x, contact + 2) || terrain.solid(x, contact + 4.5)) return true;
  }
  return false;
}

export function standOptions(terrain, obs, config, known = new Map()) {
  const actor = obs.actor;
  const current = { actor, moveX: undefined, risk: actor.grounded ? 0 : 0.1 };
  if (!obs.hole.teeActive || !obs.hole.tee) return [current];
  const { x0, x1, minFeet } = obs.hole.tee;
  const limit = Math.min(15, Math.ceil((x1 - x0) / 17) + 1);
  const grid = new Set();
  for (let i = 0; i < limit; i++) {
    const x = i === 0 ? x0 : i === limit - 1 ? x1 : 4 * Math.round((x0 + (x1 - x0) * i / (limit - 1)) / 4);
    grid.add(clamp(x, x0, x1));
  }
  for (const o of obs.objects) {
    if (!alive(o) || o.type === 'chicken') continue;
    for (const offset of [-50, 0, 50]) {
      grid.add(clamp(4 * Math.round((o.x + offset) / 4), x0, x1));
    }
  }
  const sourceFeet = actor.y + config.BODY.footOffset;
  const result = [current];
  for (const x of grid) {
    if (Math.abs(x - actor.x) < 1) continue;
    const fitted = station(terrain, x, config, minFeet);
    if (!fitted) continue;
    const previous = known.get(x);
    const plausible = previous &&
      Math.abs(previous.y - fitted.y) < 12 ? previous : fitted;
    const risk = Math.abs(plausible.groundAngle) * (previous ? 0.001 : 0.003) +
      Math.min(0.11, Math.abs(plausible.y + config.BODY.footOffset - sourceFeet) * 0.0009);
    result.push({ actor: plausible, moveX: x, risk });
  }
  return result;
}

export const movementDefaults = {
  bug: { drag: 0.955, air: 0.994, lift: 5.2, g: 0.94, gain: 1 },
  pushToken: { drag: 0.840, air: 0.987, lift: 3.2, g: 1.05, gain: 1 },
};

export function motionVariant(movement, onGrade, followingEdge, sign) {
  const original = movement.bug;
  const bug = onGrade && sign > 0
    ? { ...original, drag: 0.894, g: 0.55, climb: 90, gain: original.gain * (followingEdge ? 1.24 : 1.055),
      fallBoost: followingEdge ? 1.65 : 1 }
    : { ...original, gain: original.gain * (1 + (onGrade ? 0.22 : 0.11) * sign) };
  return { bug, pushToken: { ...movement.pushToken, gain: movement.pushToken.gain * (1 + sign * 0.18) } };
}

function splashDistance(impact, object) {
  return Math.hypot(object.x - impact.x, object.cy - impact.y);
}

export function blastEffect(terrain, impact, object, spec, movement) {
  if (!alive(object) || impact.type === 'out') return null;
  const dx = object.x - impact.x;
  const dy = object.cy - impact.y;
  const dist = splashDistance(impact, object);
  const direct = impact.object?.id === object.id;
  const movable = movement[object.type];
  const footDist = Math.hypot(dx, object.y - impact.y);
  const radius = spec.damageRadius;
  const pushRadius = object.type === 'bug' ? spec.radius : Math.max(spec.radius, radius);
  const canDamage = direct || (object.type !== 'bug' && dist < radius);
  const canPush = movable && (object.type === 'bug' ? footDist < pushRadius : dist < pushRadius);
  if (!canDamage && !canPush) return null;
  if (!direct && !terrain.visible(impact.x, impact.y, object.x, object.cy)) return null;

  const damage = object.type === 'bug' ? 0 : direct ? spec.damage
    : dist < radius ? Math.max(1, Math.round(spec.damage * (1 - dist / radius))) : 0;
  let vx = 0;
  let vy = 0;
  let travel = 0;
  if (canPush && object.type === 'bug') {
    const fraction = 1 - footDist / pushRadius;
    const down = clamp(1 - Math.max(0, dy - 3) / 19, 0.48, 1);
    const rollScale = (576 + spec.damage * 5.85) * (direct && spec.damage === 120 ? 1.06 : 1);
    travel = rollScale * movable.gain * fraction * dx / Math.max(1, footDist) * down;
    const under = clamp(-dy / object.hh, -1, 1.4);
    vy = -movable.lift * fraction * (0.30 + 0.95 * Math.max(0, under)) + Math.max(0, dy - 2) * fraction * 0.24;
    vx = travel / (1 / (1 - movable.drag) + Math.max(0, -vy) * (movable.airCompensation ?? 1.4) + 0.8);
  } else if (canPush) {
    const fraction = 1 - dist / pushRadius;
    const under = clamp(-dy / object.hh, -1, 1.4);
    const leverage = clamp(1 + 2.5 * under, 0.58, 1.55);
    travel = 99 * movable.gain * fraction * dx / Math.max(1, dist) * leverage;
    vy = -movable.lift * fraction * (0.42 + 1.55 * Math.max(0, under));
    vx = travel / (1 / (1 - movable.drag) + Math.max(0, -vy) * 1.0 + 0.8);
  }
  return { damage, vx, vy, travel, direct };
}

function support(terrain, o, x, low, high) {
  let feet = terrain.surface(x, low, high);
  const cell = terrain.cell;
  if (o.r) {
    const first = Math.floor((x - 0.96 * o.r) / cell);
    const last = Math.floor((x + 0.96 * o.r) / cell);
    for (let i = first; i <= last; i++) {
      const offset = Math.max(i * cell - x, x - (i + 1) * cell, 0);
      const rise = o.r - Math.sqrt(o.r ** 2 - offset ** 2);
      feet = Math.min(feet, terrain.surface((i + 0.5) * cell, low - rise, high - rise) + rise);
    }
  } else if (o.type === 'pushToken') {
    feet = Math.min(feet, terrain.surface(x - 0.38 * o.hw, low, high), terrain.surface(x + 0.38 * o.hw, low, high));
  }
  return feet;
}

export function objectFloor(terrain, o) {
  return support(terrain, o, o.x, o.y - 0.12, terrain.h);
}

function canRoll(terrain, o, x, tx, y) {
  if (Math.min(x, tx) - o.hw < 0 || Math.max(x, tx) + o.hw >= terrain.w ||
    Math.abs(y / terrain.cell - Math.round(y / terrain.cell)) > 0.001) return false;
  const a = Math.floor((Math.min(x, tx) - o.hw) / terrain.cell);
  const b = Math.floor((Math.max(x, tx) + o.hw) / terrain.cell);
  const row = Math.round(y / terrain.cell) * terrain.width;
  const upper = Math.floor((y - 2 * o.hh - 1) / terrain.cell);
  for (let col = a; col <= b; col++) {
    if (!terrain.cells[row + col]) return false;
    for (let r = row - terrain.width; r >= upper * terrain.width && r >= 0; r -= terrain.width) {
      if (terrain.cells[r + col]) return false;
    }
  }
  return true;
}

export function predictRest(o, terrain, vx, vy, physics, ticks = 265) {
  let x = o.x, y = o.y;
  if (!vx && !vy && ticks >= 200) {
    const ground = support(terrain, o, x, y - 0.15, terrain.h);
    return Number.isFinite(ground) ? { x, y: ground, vx: 0, vy: 0, out: false, stopped: true }
      : { x, y: terrain.h, vx: 0, vy: 0, out: true, stopped: false };
  }

  const side = o.hw * (o.r ? 0.84 : 0.96);
  const friction = physics?.drag ?? 0.84;
  const gravity = physics?.g ?? 1;
  const climb = physics?.climb ?? 16;
  let floor = !vy && support(terrain, o, x, y - 0.18, y + 0.45) <= y + 0.45;
  for (let tick = 0; tick < ticks; tick++) {
    if (floor && vy >= 0 && tick % 6 === 1 && Math.abs(vx) > 0.13) {
      const stopTicks = Math.max(1, Math.ceil(Math.log(0.13 / Math.abs(vx)) / Math.log(friction)));
      const n = Math.min(stopTicks, ticks - tick);
      const decay = friction ** n;
      const tx = x + vx * (1 - decay) / (1 - friction);
      if (canRoll(terrain, o, x, tx, y)) return { x: tx, y, vx: vx * decay, vy: 0, out: false, stopped: n === stopTicks };
    }

    // Check intermediate horizontal positions so fast rolls still see narrow walls.
    const segmentCount = Math.max(1, Math.ceil(Math.abs(vx) / 7));
    const horizontal = vx / segmentCount;
    for (let i = 0; i < segmentCount; i++) {
      const nx = x + horizontal;
      if (nx < 0 || nx >= terrain.w || y >= terrain.h) return { x: nx, y, vx, vy, out: true, stopped: false };
      const leading = vx >= 0 ? side : -side;
      const occupied = vx && terrain.solid(nx + leading, y - o.hh);
      if (occupied && climb > 20) {
        const top = terrain.columnTop(nx + leading, y - o.hh);
        if (y - top <= climb) {
          y = top;
          vy = Math.min(0, vy);
          floor = true;
        } else {
          vx = 0;
          break;
        }
      } else if (occupied) {
        const rise = support(terrain, o, nx, y - climb, y);
        if (floor && Number.isFinite(rise) && rise < y && !terrain.solid(nx + leading, rise - o.hh)) y = rise;
        else {
          vx = 0;
          break;
        }
      }
      if (floor && Math.abs(horizontal) > 0.01) {
        const step = support(terrain, o, nx, y - climb, y + 0.55);
        if (step < y - 0.4) y = step;
      }
      x = nx;
    }
    const wasOnFloor = floor;
    const nextY = y + vy + 0.5 * gravity;
    if (vy < -0.5 && terrain.solid(x, nextY - 2 * o.hh)) {
      vy = 0;
      floor = false;
    } else if (nextY >= y) {
      const land = support(terrain, o, x, y - 4.5, nextY + 0.04);
      if (land <= nextY) {
        y = land;
        vy = 0;
        floor = true;
      } else {
        y = nextY;
        vy += gravity;
        floor = false;
        if (wasOnFloor && physics?.fallBoost > 1 && support(terrain, o, x, y + 0.2, y + 32) === Infinity) {
          vx *= physics.fallBoost;
        }
      }
    } else {
      y = nextY;
      vy += gravity;
      floor = false;
    }
    vx *= floor ? friction : (physics?.air ?? 0.992);
    if (floor && Math.abs(vx) < 0.13) return { x, y, vx: 0, vy: 0, out: false, stopped: true };
    if (y >= terrain.h) return { x, y, vx, vy, out: true, stopped: false };
  }
  return { x, y, vx, vy, out: false, stopped: false };
}

export function advanceWorld(state, environment, ticks = 265) {
  for (const o of state.objects) {
    if (!alive(o) || !o.moving) continue;
    const rest = predictRest(o, state.terrain, o.motionX, o.motionY, environment.movement[o.type], ticks);
    if (rest.out) {
      if (o.type === 'bug') {
        state.penalties++;
        state.bugOut = true;
        setPosition(o, o.preShotX, o.preShotY);
      } else {
        o.dead = true;
        if (o.type === 'chicken') state.penalties++;
      }
      o.moving = false;
      o.motionX = 0;
      o.motionY = 0;
      continue;
    }
    setPosition(o, rest.x, rest.y);
    o.moving = !rest.stopped;
    o.motionX = rest.vx;
    o.motionY = rest.vy;
    if (o.type === 'bug' && rest.stopped && inside(o, environment.box)) o.inRecycle = true;
  }
}

export function applyImpact(state, impact, spec, environment, ticks = 265) {
  if (impact.type === 'out') return;
  const { movement } = environment;
  const { terrain, objects } = state;
  const effects = objects.map(o => blastEffect(terrain, impact, o, spec, movement));
  if (!state.edited) {
    state.terrain = terrain.clone();
    state.edited = true;
  }
  state.carved = (state.carved ?? 0) + state.terrain.carve(impact.x, impact.y, spec.radius);
  for (let i = 0; i < objects.length; i++) {
    const o = objects[i];
    if (!alive(o)) continue;
    const e = effects[i];
    if (e?.damage > 0 && o.type !== 'bug') {
      o.hp = Math.max(0, (o.hp ?? 1) - e.damage);
      if (o.type === 'chicken' || o.hp === 0) {
        o.dead = true;
        if (o.type === 'chicken') state.penalties++;
        continue;
      }
    }
    const d = Math.hypot(o.x - impact.x, o.y - impact.y);
    if (!e?.vx && !e?.vy && d > spec.radius + o.hw + 4) continue;
    o.moving = true;
    o.motionX = (o.motionX ?? 0) + (e?.vx ?? 0);
    o.motionY = (o.motionY ?? 0) + (e?.vy ?? 0);
  }
  if (ticks > 0) advanceWorld(state, environment, ticks);
}

export function distanceToBox(x, y, box) {
  if (!box) return 0;
  return Math.hypot(Math.max(box.x0 - x, x - box.x1, 0),
    Math.max(box.y0 - y, y - box.y1, 0) * 1.08);
}
