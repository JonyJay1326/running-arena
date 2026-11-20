import { CONFIG as C, windFactor } from './config.mjs';
import { muzzlePosition, solidAt } from './geometry.mjs';
import { objectDistance } from './objects.mjs';

export function projectile(actor, aim, { kind = 'single', offset = 0, id = 0, plane = false, start = 0 } = {}) {
  const worldAngle = aim.angle + offset + (plane ? 0 : aim.dir * actor.groundAngle);
  const a = worldAngle * Math.PI / 180, muzzle = muzzlePosition(actor);
  return { id, kind, plane, worldAngle, ...muzzle, vx: Math.cos(a) * aim.power * C.V_K * aim.dir, vy: -Math.sin(a) * aim.power * C.V_K,
    active: true, start, elapsed: 0, pts: [[muzzle.x, muzzle.y]], times: [start] };
}
export function stepProjectile(p, { grid, objects = [], wind, factor = windFactor }, dt, time) {
  if (!p.active) return null;
  p.vx += wind * C.WIND_K * factor(p.y) * dt; p.vy += C.G * dt;
  p.x += p.vx * dt; p.y += p.vy * dt; p.elapsed += dt;
  const out = p.x < 0 || p.x >= C.W || p.y >= C.H;
  const target = !out && !p.plane ? objects.find(o => !o.removed && !o.inRecycle && objectDistance(o, p.x, p.y) <= 1e-7) : null;
  const terrain = !out && solidAt(grid, p.x, p.y);
  if (!out && !target && !terrain) return null;
  p.active = false;
  p.land = { x: p.x, y: p.y, hit: out ? 'out' : target ? target.type : 'terrain', objectId: target?.id ?? null };
  sampleProjectile(p, time);
  return { ...p.land, out, target, terrain, shot: p };
}
export function sampleProjectile(p, time) {
  if (p.times.at(-1) !== time) { p.pts.push([p.x, p.y]); p.times.push(time); }
}
export function traceShot(grid, actor, wind, aim, { factor = windFactor, objects = [], ...options } = {}) {
  const p = projectile(actor, aim, options);
  for (let tick = 0; tick < 1200; tick++) {
    const n = Math.ceil(Math.max(1000, Math.hypot(p.vx, p.vy) + C.G * C.TICK) * C.TICK / 1.5);
    for (let sub = 0; sub < n && p.active; sub++) stepProjectile(p, { grid, objects, wind, factor }, C.TICK / n, (tick + (sub + 1) / n) * C.TICK);
    if (!p.active) return p;
    sampleProjectile(p, (tick + 1) * C.TICK);
  }
  throw new Error('弹道超过20秒');
}
