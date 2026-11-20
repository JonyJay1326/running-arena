export const DEG = Math.PI / 180;
export const VELOCITY = Math.sqrt(0.12);
export const WIND_FACTOR = 0.0375;
const WIND_TOP = 355;
const WIND_BOTTOM = 445;
const WIND_GAIN = 0.35;

export const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));

export function muzzle(actor, body) {
  const tilt = actor.groundAngle * DEG;
  return {
    x: actor.x - body.muzzleHeight * Math.sin(tilt),
    y: actor.y + body.footOffset - body.muzzleHeight * Math.cos(tilt),
  };
}

// Integral of (t-s) * max(0, level-y(s)) over [0,t].
function altitudeMoment(level, t, vy, apex, halfWidth) {
  if (halfWidth === 0) return 0;
  const a = Math.max(vy, -halfWidth);
  const b = Math.min(t + vy, halfWidth);
  if (b <= a) return 0;
  const range = b - a;
  const height = level - apex;
  const a2 = a * a;
  const b2 = b * b;
  const area = range * (height - (a2 + a * b + b2) / 6);
  const centerMoment = (b2 - a2) * (height / 2 - (a2 + b2) / 8);
  return (t + vy) * area - centerMoment;
}

function altitudeIntegral(level, t, vy, apex, halfWidth) {
  if (halfWidth === 0) return 0;
  const a = Math.max(vy, -halfWidth);
  const b = Math.min(t + vy, halfWidth);
  return b <= a ? 0 : (b - a) * (level - apex - (a * a + a * b + b * b) / 6);
}

function airProfile(y, vy) {
  const apex = y - vy * vy / 2;
  return {
    apex,
    low: Math.sqrt(Math.max(0, 2 * (WIND_BOTTOM - apex))),
    high: Math.sqrt(Math.max(0, 2 * (WIND_TOP - apex))),
  };
}

function drift(t, vy, wind, profile) {
  if (!wind) return 0;
  let displacement = t * t / 2;
  if (profile.low) {
    displacement += WIND_GAIN * (
      altitudeMoment(WIND_BOTTOM, t, vy, profile.apex, profile.low) -
      altitudeMoment(WIND_TOP, t, vy, profile.apex, profile.high)
    ) / (WIND_BOTTOM - WIND_TOP);
  }
  return wind * WIND_FACTOR * displacement;
}

export function aimVelocity(from, tx, ty, t, wind) {
  const vy = (ty - from.y) / t - t / 2;
  const profile = wind ? airProfile(from.y, vy) : null;
  const vx = (tx - from.x - drift(t, vy, wind, profile)) / t;
  return { vx, vy };
}

export class Path {
  constructor(from, vx, vy, wind) {
    this.x0 = from.x;
    this.y0 = from.y;
    this.vx = vx;
    this.vy = vy;
    this.wind = wind;
    this.profile = wind ? airProfile(from.y, vy) : null;
    this.peakY = vy < 0 ? from.y - vy * vy / 2 : from.y;
  }

  static shot(from, degrees, power, dir, wind) {
    const speed = power * VELOCITY;
    const a = degrees * DEG;
    return new Path(from, dir * speed * Math.cos(a), -speed * Math.sin(a), wind);
  }

  x(t) {
    return this.x0 + this.vx * t + drift(t, this.vy, this.wind, this.profile);
  }

  y(t) {
    return this.y0 + (this.vy + t / 2) * t;
  }

  horizontalSpeed(t) {
    if (!this.wind) return this.vx;
    const { low, high, apex } = this.profile;
    const extra = (altitudeIntegral(WIND_BOTTOM, t, this.vy, apex, low) -
      altitudeIntegral(WIND_TOP, t, this.vy, apex, high)) / (WIND_BOTTOM - WIND_TOP);
    return this.vx + WIND_FACTOR * this.wind * (t + WIND_GAIN * extra);
  }

  descendTo(y) {
    const discriminant = this.vy * this.vy + 2 * (y - this.y0);
    return discriminant < 0 ? Infinity : -this.vy + Math.sqrt(discriminant);
  }
}

function rectangleHit(x, y, dx, dy, o, limit) {
  let begin = 0;
  let end = limit;
  for (let axis = 0; axis < 2; axis++) {
    const start = axis ? y : x;
    const d = axis ? dy : dx;
    const min = axis ? o.top : o.left;
    const max = axis ? o.bottom : o.right;
    if (d === 0) {
      if (start < min || start > max) return Infinity;
      continue;
    }
    let a = (min - start) / d;
    let b = (max - start) / d;
    if (a > b) [a, b] = [b, a];
    begin = Math.max(begin, a);
    end = Math.min(end, b);
    if (end < begin) return Infinity;
  }
  return begin;
}

function circleHit(x, y, dx, dy, o, limit) {
  const px = x - o.x;
  const py = y - o.cy;
  const c = px * px + py * py - o.r * o.r;
  if (c <= 0) return 0;
  const b = px * dx + py * dy;
  if (b >= 0) return Infinity;
  const a = dx * dx + dy * dy;
  const discriminant = b * b - a * c;
  if (discriminant < 0 || !a) return Infinity;
  const t = (-b - Math.sqrt(discriminant)) / a;
  return t >= 0 && t <= limit ? t : Infinity;
}

export class Terrain {
  constructor(data, cells) {
    this.width = data.width;
    this.height = data.height;
    this.cell = data.cell;
    this.w = this.width * this.cell;
    this.h = this.height * this.cell;
    this.cells = cells ?? new Uint8Array(this.width * this.height);
    if (!cells) {
      let index = 0;
      for (const [value, count] of data.rle) {
        if (value) this.cells.fill(1, index, index + count);
        index += count;
      }
    }
    const blockSize = 8;
    this.blockSize = blockSize;
    this.blockWorld = this.cell * blockSize;
    this.blockWidth = Math.ceil(this.width / blockSize);
    this.blocks = new Uint8Array(this.blockWidth * Math.ceil(this.height / blockSize));
    const stride = this.width;
    for (let cy = 0; cy < this.height; cy++) {
      const blockRow = (cy >> 3) * this.blockWidth;
      const index = cy * stride;
      for (let cx = 0; cx < stride; cx++) {
        if (this.cells[index + cx]) this.blocks[blockRow + (cx >> 3)] = 1;
      }
    }
  }

  clone() {
    const t = Object.create(Terrain.prototype);
    t.width = this.width;
    t.height = this.height;
    t.cell = this.cell;
    t.w = this.w;
    t.h = this.h;
    t.cells = this.cells.slice();
    t.blockSize = this.blockSize;
    t.blockWorld = this.blockWorld;
    t.blockWidth = this.blockWidth;
    t.blocks = this.blocks;
    t.ceiling = this.ceiling;
    return t;
  }

  solid(x, y) {
    if (x < 0 || x >= this.w || y < 0 || y >= this.h) return false;
    return this.cells[(y / this.cell | 0) * this.width + (x / this.cell | 0)] !== 0;
  }

  surface(x, y0 = 0, y1 = this.h) {
    if (x < 0 || x >= this.w || y0 >= this.h) return Infinity;
    const cx = x / this.cell | 0;
    const begin = Math.max(0, Math.floor(y0 / this.cell));
    const end = Math.min(this.height - 1, Math.floor(y1 / this.cell));
    for (let cy = begin; cy <= end; cy++) {
      if (this.cells[cy * this.width + cx]) return cy * this.cell;
    }
    return Infinity;
  }

  columnTop(x, y) {
    if (!this.solid(x, y)) return Infinity;
    const cx = x / this.cell | 0;
    let row = y / this.cell | 0;
    while (row > 0 && this.cells[(row - 1) * this.width + cx]) row--;
    return row * this.cell;
  }

  carve(x, y, radius) {
    const d = this.cell;
    const minX = Math.max(0, Math.ceil((x - radius) / d - 0.5));
    const maxX = Math.min(this.width - 1, Math.floor((x + radius) / d - 0.5));
    const minY = Math.max(0, Math.ceil((y - radius) / d - 0.5));
    const maxY = Math.min(this.height - 1, Math.floor((y + radius) / d - 0.5));
    const r2 = radius * radius;
    let changed = 0;
    for (let cy = minY; cy <= maxY; cy++) {
      const dy = (cy + 0.5) * d - y;
      const row = cy * this.width;
      for (let cx = minX; cx <= maxX; cx++) {
        const dx = (cx + 0.5) * d - x;
        if (this.cells[row + cx] && dx * dx + dy * dy <= r2) {
          this.cells[row + cx] = 0;
          changed++;
        }
      }
    }
    return changed;
  }

  crossesSolid(x, y, dx, dy, limit = 1) {
    const bx = x + dx * limit;
    const by = y + dy * limit;
    const block = this.blockWorld;
    const qx0 = Math.max(0, Math.floor(Math.min(x, bx) / block));
    const qx1 = Math.min(this.blockWidth - 1, Math.floor(Math.max(x, bx) / block));
    const qy0 = Math.max(0, Math.floor(Math.min(y, by) / block));
    const qy1 = Math.min(Math.ceil(this.height / this.blockSize) - 1, Math.floor(Math.max(y, by) / block));
    let possible = false;
    for (let qy = qy0; qy <= qy1 && !possible; qy++) {
      for (let qx = qx0; qx <= qx1; qx++) {
        if (this.blocks[qy * this.blockWidth + qx]) {
          possible = true;
          break;
        }
      }
    }
    if (!possible) return Infinity;

    const d = this.cell;
    let cx = Math.floor(x / d);
    let cy = Math.floor(y / d);
    const sx = Math.sign(dx);
    const sy = Math.sign(dy);
    const tx = dx ? d / Math.abs(dx) : Infinity;
    const ty = dy ? d / Math.abs(dy) : Infinity;
    let nx = dx ? ((cx + (sx > 0 ? 1 : 0)) * d - x) / dx : Infinity;
    let ny = dy ? ((cy + (sy > 0 ? 1 : 0)) * d - y) / dy : Infinity;
    let t = 0;
    while (t <= limit) {
      if (cx >= 0 && cx < this.width && cy >= 0 && cy < this.height && this.cells[cy * this.width + cx]) return t;
      if (nx < ny) {
        t = nx;
        cx += sx;
        nx += tx;
      } else {
        t = ny;
        cy += sy;
        ny += ty;
      }
    }
    return Infinity;
  }

  visible(x, y, tx, ty) {
    const dx = tx - x;
    const dy = ty - y;
    const dist = Math.hypot(dx, dy);
    if (dist < 5) return true;
    const skip = 4.5 / dist;
    return this.crossesSolid(x + dx * skip, y + dy * skip, dx * (1 - skip), dy * (1 - skip), 0.96) === Infinity;
  }
}

export function shape(obj, defaults = {}) {
  const r = obj.radius ?? defaults.radius ?? (obj.type === 'bug' ? 14 : 0);
  const w = obj.width ?? defaults.width ?? (obj.type === 'pushToken' ? 30 : 28);
  const h = obj.height ?? defaults.height ?? (obj.type === 'pushToken' ? 30 : 28);
  const hh = r || h / 2;
  const hw = r || w / 2;
  return {
    ...obj, r, hw, hh, cy: obj.y - hh,
    left: obj.x - hw, right: obj.x + hw, top: obj.y - 2 * hh, bottom: obj.y,
  };
}

export function setPosition(o, x, y) {
  o.x = x;
  o.y = y;
  o.cy = y - o.hh;
  o.left = x - o.hw;
  o.right = x + o.hw;
  o.top = y - 2 * o.hh;
  o.bottom = y;
}

export function trace(path, terrain, objects, start = 0, options = {}) {
  const flightEnd = Math.min(116, path.descendTo(terrain.h) + 0.02);
  const maxT = Math.min(flightEnd, options.end ?? Infinity);
  const dt = options.step ?? 1.8;
  let t = start;
  let x = path.x(t);
  let y = path.y(t);
  for (; t < maxT; ) {
    const next = Math.min(t + dt, maxT);
    const bx = path.x(next);
    const by = path.y(next);
    const dx = bx - x;
    const dy = by - y;
    let out = Infinity;
    if (bx < 0) out = -x / dx;
    if (bx >= terrain.w) out = (terrain.w - x) / dx;
    if (by >= terrain.h && dy > 0) out = Math.min(out, (terrain.h - y) / dy);
    const ceiling = options.ceiling ?? terrain.ceiling ?? -Infinity;
    if (by < ceiling && dy < 0) out = Math.min(out, (ceiling - y) / dy);
    const rayLimit = Math.min(1, out);
    let fraction = terrain.crossesSolid(x, y, dx, dy, rayLimit);
    let type = 'ground';
    let object = null;
    const lx = Math.min(x, bx), rx = Math.max(x, bx);
    const ty = Math.min(y, by), ly = Math.max(y, by);
    for (let i = 0; i < objects.length; i++) {
      const o = objects[i];
      if (o.dead || o.removed || o.broken || o.stunned || o.inRecycle ||
          o.left > rx || o.right < lx || o.top > ly || o.bottom < ty) continue;
      const hit = o.r ? circleHit(x, y, dx, dy, o, Math.min(rayLimit, fraction))
        : rectangleHit(x, y, dx, dy, o, Math.min(rayLimit, fraction));
      if (hit < fraction) {
        fraction = hit;
        type = 'object';
        object = o;
      }
    }
    if (fraction === Infinity && out <= 1) {
      fraction = clamp(out, 0, 1);
      type = 'out';
    }
    if (fraction < Infinity) {
      return { x: x + fraction * dx, y: y + fraction * dy, t: t + fraction * (next - t), type, object };
    }
    t = next;
    x = bx;
    y = by;
  }
  return { x, y, t, type: maxT < flightEnd ? 'flying' : 'out', object: null };
}

export function exam(question, ground, config) {
  const start = { x: question.x, y: ground - config.BODY.muzzleHeight };
  const path = Path.shot(start, question.angle, question.power, question.dir, question.wind);
  const tGround = path.descendTo(ground);
  const segments = [0];
  // Wind points in a constant direction, so x(t) can turn at most once.
  if (path.vx * path.horizontalSpeed(tGround) < 0) {
    let lo = 0, hi = tGround;
    for (let i = 0; i < 22; i++) {
      const mid = (lo + hi) / 2;
      if (path.horizontalSpeed(mid) * path.vx > 0) lo = mid;
      else hi = mid;
    }
    segments.push((lo + hi) / 2);
  }
  segments.push(tGround);
  for (let j = 1; j < segments.length; j++) {
    const xEnd = path.x(segments[j]);
    if (xEnd >= 0 && xEnd < config.W) continue;
    const boundary = xEnd < 0 ? 0 : config.W;
    let lo = segments[j - 1], hi = segments[j];
    for (let i = 0; i < 22; i++) {
      const mid = (lo + hi) / 2;
      if (boundary === 0 ? path.x(mid) < 0 : path.x(mid) >= config.W) hi = mid;
      else lo = mid;
    }
    return { id: question.id, x: boundary, y: path.y(hi) };
  }
  return { id: question.id, x: path.x(tGround), y: ground };
}
