export const MODEL = { speed: Math.sqrt(0.12), wind: 0.0375, highY: 400, highExtra: 0.35 };
export function trajectory(x, y, angle, power, dir, wind, model = MODEL) {
  const a = angle * Math.PI / 180;
  const speed = model.speed * power;
  const vx = dir * Math.cos(a) * speed;
  const up = Math.sin(a) * speed;
  const acc = wind * model.wind;
  const disc = up * up - 2 * (y - model.highY);
  let enter = Infinity, leave = Infinity;
  if (disc > 0) {
    enter = Math.max(0, up - Math.sqrt(disc));
    leave = Math.max(0, up + Math.sqrt(disc));
  }
  return {
    vx, up, speed,
    at(t) {
      const end = Math.min(t, leave);
      const extra = end > enter ? model.highExtra * acc * (t * (end - enter) - (end * end - enter * enter) / 2) : 0;
      return { x: x + vx * t + acc * t * t / 2 + extra, y: y - up * t + t * t / 2 };
    },
    timeAtY(targetY) {
      const d = up * up + 2 * (targetY - y);
      return d >= 0 ? up + Math.sqrt(d) : NaN;
    }
  };
}
export function predictFlat(q, ground = 780, muzzleHeight = 35) {
  const path = trajectory(q.x, ground - muzzleHeight, q.angle, q.power, q.dir, q.wind);
  const t = path.timeAtY(ground);
  const land = path.at(t);
  // A projectile leaving the horizontal map is removed before it reaches ground.
  {
    let previousT = 0;
    for (let s = 0.1; s <= t + 0.1; s += 0.1) {
      const p = path.at(s);
      if (p.x < 0 || p.x > 1600 || p.y > 900) {
        let lo = previousT, hi = s;
        for (let k = 0; k < 20; k++) {
          const m = (lo + hi) / 2, v = path.at(m);
          if (v.x < 0 || v.x > 1600 || v.y > 900) hi = m; else lo = m;
        }
        return path.at(hi);
      }
      previousT = s;
    }
  }
  return land;
}
