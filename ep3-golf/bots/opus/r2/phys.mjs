// Physics model fitted from probes (see NOTES.md). Units: g = 1, v0 = sqrt(0.12)*power,
// wind accel = WIND_K * wind * windHeightFactor(y) * windScale.
export const VK = Math.sqrt(0.12);
export const WIND_K = 0.03754;
export function windHeightFactor(y) {
  return 1 + 0.35 * Math.min(1, Math.max(0, (440 - y) / 80));
}
export const MUZZLE = 35, FOOT = 12;

// muzzle and velocity for an attack/plane given foot position and ground angle
export function launch(footX, footY, groundAngle, angle, power, dir, isPlane = false) {
  const gA = isPlane ? 0 : groundAngle;
  const gr = groundAngle * Math.PI / 180;
  const mx = footX - MUZZLE * Math.sin(gr);
  const my = footY - MUZZLE * Math.cos(gr);
  const act = (angle + dir * gA) * Math.PI / 180;
  const v = VK * power;
  return { x: mx, y: my, vx: dir * v * Math.cos(act), vy: -v * Math.sin(act) };
}

// Simple ballistic flight on flat ground (exam). Returns landing point at ground.
export function flatLanding(wind, x, angle, power, dir, ground = 780, windScale = 1) {
  const L = launch(x, ground, 0, angle, power, dir);
  let { x: px, y: py, vx, vy } = L;
  for (let i = 0; i < 200000; i++) {
    const sp = Math.hypot(vx, vy) + 1e-9;
    const dt = Math.min(0.05, 1 / sp);
    const ax = WIND_K * wind * windHeightFactor(py) * windScale;
    const nvx = vx + ax * dt, nvy = vy + dt;
    const nx = px + (vx + nvx) / 2 * dt, ny = py + (vy + nvy) / 2 * dt;
    if (ny >= ground) {
      const f = (ground - py) / (ny - py);
      return { x: px + (nx - px) * f, y: ground };
    }
    px = nx; py = ny; vx = nvx; vy = nvy;
  }
  return { x: px, y: py };
}
