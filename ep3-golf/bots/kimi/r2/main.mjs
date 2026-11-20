import { createInterface } from 'node:readline';

// Effective flat-ground constants fitted with the 16 authorised probes.
const V0 = 0.25;
const GRAVITY = 0.52;
const WIND = 0.0195;
const CELL = 4;
const MAX_TICKS = 260;
const rad = d => d * Math.PI / 180;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const dist = (x0, y0, x1, y1) => Math.hypot(x1 - x0, y1 - y0);

function decodeTerrain(t) {
  const n = t.width * t.height;
  const cells = new Uint8Array(n);
  let p = 0;
  for (const pair of t.rle) {
    const value = pair[0] ? 1 : 0;
    const end = Math.min(n, p + pair[1]);
    if (value) cells.fill(1, p, end);
    p = end;
  }
  return { cells, width: t.width, height: t.height };
}

function windFactor(y) {
  // The highest probe showed extra wind above the ordinary flight envelope.
  // Its exact law remains unknown; this conservative step fits that observation.
  return y < 400 ? 1.344 : 1;
}

function examPrediction(q, ground) {
  const a = rad(q.angle);
  const v = V0 * q.power;
  const vx0 = q.dir * v * Math.cos(a);
  const vy0 = -v * Math.sin(a);
  const h = 35;
  const flight = (-vy0 + Math.sqrt(vy0 * vy0 + 2 * GRAVITY * h)) / GRAVITY;
  let x = q.x;
  let vx = vx0;
  const dt = 0.1;
  for (let t = 0; t < flight; t += dt) {
    const step = Math.min(dt, flight - t);
    const y = ground - h + vy0 * t + 0.5 * GRAVITY * t * t;
    const ax = q.wind * WIND * windFactor(y);
    x += vx * step + 0.5 * ax * step * step;
    vx += ax * step;
  }
  // The probe collision reports have a sub-pixel, direction-symmetric bias.
  x -= q.dir * 0.45;
  return { id: q.id, x: Math.round(x * 100) / 100, y: ground + 0.2 };
}

function objectCenter(o) {
  return { x: o.x, y: o.y - (o.type === 'bug' ? (o.radius || 14) : (o.height || 28) / 2) };
}

function active(o) {
  return !o.removed && !o.broken && !o.inRecycle;
}

function shouldDropBug(bug, zone) {
  if (!bug || !zone || (zone.y0 + zone.y1) / 2 <= bug.y + 30) return false;
  const horizontallyAligned = bug.x >= zone.x0 && bug.x <= zone.x1;
  const veryDeep = (zone.y0 + zone.y1) / 2 - bug.y > 150;
  return horizontallyAligned || veryDeep;
}

class Bot {
  constructor(init) {
    this.config = init.config || {};
    this.biasX = 0;
    this.biasY = 0;
    this.lastPlan = null;
    this.failedActions = [];
    this.turn = 0;
  }

  support(terrain, x, currentFoot, minFeet = -Infinity) {
    const col = clamp(Math.floor(x / CELL), 0, terrain.width - 1);
    const surfaces = [];
    for (let row = 1; row < terrain.height; row++) {
      const i = row * terrain.width + col;
      if (terrain.cells[i] && !terrain.cells[i - terrain.width]) {
        const foot = row * CELL;
        let clear = true;
        for (let y = foot - CELL; y >= foot - 56 && y >= 0; y -= CELL) {
          if (terrain.cells[Math.floor(y / CELL) * terrain.width + col]) { clear = false; break; }
        }
        if (clear && foot >= minFeet && foot > 60) surfaces.push(foot);
      }
    }
    if (!surfaces.length) return null;
    surfaces.sort((a, b) => Math.abs(a - currentFoot) - Math.abs(b - currentFoot));
    return surfaces[0];
  }

  stands(r, terrain) {
    // The observed actor is always legal and has the engine's true slope. Estimated
    // destination slopes caused invalid moves and reversed shots on cratered ramps.
    const actor = r.actor;
    const foot = actor.y + (this.config.BODY?.footOffset || 12);
    return [{ x: actor.x, foot, angle: actor.groundAngle || 0, move: false }];
  }

  isSolid(terrain, x, y) {
    if (x < 0 || x >= 1600 || y >= 900 || y < 0) return false;
    const col = Math.floor(x / CELL);
    const row = Math.floor(y / CELL);
    return terrain.cells[row * terrain.width + col] !== 0;
  }

  clearLine(terrain, x0, y0, x1, y1) {
    const d = dist(x0, y0, x1, y1);
    const n = Math.ceil(d / 3);
    for (let i = 1; i < n; i++) {
      const t = i / n;
      if (this.isSolid(terrain, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)) return false;
    }
    return true;
  }

  simulate(r, terrain, stand, attack, angleOffset = 0) {
    const body = this.config.BODY || { muzzleHeight: 35 };
    const tilt = rad(stand.angle);
    const h = body.muzzleHeight || 35;
    let x = stand.x + Math.sin(tilt) * h;
    let y = stand.foot - Math.cos(tilt) * h;
    const protocolAngle = clamp(attack.angle + angleOffset, 0, 85);
    const actual = rad(protocolAngle + attack.dir * stand.angle);
    const speed = attack.power * V0;
    let vx = attack.dir * speed * Math.cos(actual);
    let vy = -speed * Math.sin(actual);
    const objects = r.objects.filter(active);
    let elapsed = 0;
    const dt = 0.5;
    while (elapsed < MAX_TICKS) {
      const oldX = x, oldY = y;
      const ax = r.hole.wind * WIND * windFactor(y);
      x += vx * dt + 0.5 * ax * dt * dt;
      y += vy * dt + 0.5 * GRAVITY * dt * dt;
      vx += ax * dt;
      vy += GRAVITY * dt;
      elapsed += dt;
      if (x < -20 || x > 1620 || y > 920) return { x, y, hit: 'out', elapsed };
      const length = dist(oldX, oldY, x, y);
      const steps = Math.max(1, Math.ceil(length / 2));
      for (let s = 1; s <= steps; s++) {
        const f = s / steps;
        const px = oldX + (x - oldX) * f;
        const py = oldY + (y - oldY) * f;
        for (const o of objects) {
          let collision = false;
          if (o.type === 'bug') {
            collision = dist(px, py, o.x, o.y - (o.radius || 14)) <= (o.radius || 14);
          } else {
            const w = o.width || (o.type === 'bigToken' ? 44 : o.type === 'pushToken' ? 30 : 28);
            const hh = o.height || (o.type === 'bigToken' ? 56 : o.type === 'chicken' ? 30 : 28);
            collision = px >= o.x - w / 2 && px <= o.x + w / 2 && py >= o.y - hh && py <= o.y;
          }
          if (collision) return { x: px, y: py, hit: 'object', object: o, elapsed: elapsed - dt + f * dt };
        }
        if (px < 0 || px >= 1600 || py >= 900) return { x: px, y: py, hit: 'out', elapsed: elapsed - dt + f * dt };
        if (this.isSolid(terrain, px, py)) return { x: px, y: py, hit: 'ground', elapsed: elapsed - dt + f * dt };
      }
    }
    return { x, y, hit: 'out', elapsed };
  }

  tokenScore(r, terrain, impact) {
    if (impact.hit === 'out') return 1e7;
    let score = 400;
    const targets = r.objects.filter(o => active(o) && ['token', 'bigToken', 'pushToken'].includes(o.type));
    if (impact.hit === 'object') {
      if (impact.object.type === 'chicken') return 100000;
      if (impact.object.type === 'bug') score = 1800;
      else score = 0;
    }
    let nearest = Infinity;
    for (const o of targets) {
      const c = objectCenter(o);
      const d = dist(impact.x + this.biasX, impact.y + this.biasY, c.x, c.y);
      nearest = Math.min(nearest, d);
      if (impact.hit === 'ground' && d <= 31 && this.clearLine(terrain, impact.x, impact.y, c.x, c.y)) {
        score = Math.min(score, 8 + d * 0.05);
      }
    }
    if (score >= 400) score += Number.isFinite(nearest) ? nearest : 1000;
    for (const o of r.objects) {
      if (!active(o) || o.type !== 'chicken') continue;
      const c = objectCenter(o);
      const d = dist(impact.x, impact.y, c.x, c.y);
      // Collateral costs one stroke, but must not make a reachable goal worse than endless safe misses.
      if (d < 48) score += 350 + (48 - d) * 2;
    }
    return score;
  }

  bugScore(r, terrain, impact) {
    if (impact.hit === 'out') return 1e7;
    const bug = r.objects.find(o => active(o) && o.type === 'bug');
    if (!bug) return 1e7;
    const zone = r.hole.recycle;
    if (!zone) return 1e7;
    const zx = (zone.x0 + zone.x1) / 2, zy = (zone.y0 + zone.y1) / 2;
    const bx = bug.x, by = bug.y - (bug.radius || 14);
    if (shouldDropBug(bug, zone)) {
      // For an aligned or very deep target, open support with single craters.
      // Dig was rejected after real practice: even a centered shot slid the Bug 640 px.
      const idealX = bug.x;
      const idealY = bug.y + 8;
      let dropScore;
      if (impact.hit === 'ground') {
        dropScore = dist(impact.x, impact.y, idealX, idealY) * 2 + (impact.y < bug.y ? 200 : 0);
      } else if (impact.hit === 'object') {
        dropScore = impact.object.type === 'chicken' ? 100000 : 1200;
      } else {
        dropScore = 1e7;
      }
      for (const o of r.objects) {
        if (active(o) && o.type === 'chicken' && dist(impact.x, impact.y, o.x, o.y - 15) < 48) dropScore += 500;
      }
      return dropScore;
    }
    const zd = Math.max(1, dist(bx, by, zx, zy));
    const ux = (zx - bx) / zd, uy = (zy - by) / zd;
    const vx = bx - impact.x, vy = by - impact.y;
    const vd = Math.max(1, Math.hypot(vx, vy));
    const alignment = (vx * ux + vy * uy) / vd;
    // A close flat-ground blast can slide a Bug more than 500 px (lab 1).
    // Prefer a near-edge splash over a direct impact so the next turn remains recoverable.
    const blastDistance = 29;
    const idealX = bx - ux * blastDistance, idealY = by - uy * blastDistance;
    let score;
    if (impact.hit === 'object' && impact.object.type === 'bug') {
      score = 260 + (1 - alignment) * 240 + Math.max(0, vd - 30) * 10;
    } else if (impact.hit === 'object') {
      score = impact.object.type === 'chicken' ? 100000 : 1500;
    } else {
      score = 30 + dist(impact.x + this.biasX, impact.y + this.biasY, idealX, idealY) * 2 + (1 - alignment) * 100;
      if (vd > 55) score += 350;
    }
    for (const o of r.objects) {
      if (active(o) && o.type === 'chicken' && dist(impact.x, impact.y, o.x, o.y - 15) < 48) score += 500;
    }
    return score;
  }

  chooseKind(r, impact, score, mode) {
    if (mode === 'bug') {
      const bug = r.objects.find(o => active(o) && o.type === 'bug');
      const c = bug && objectCenter(bug);
      const zone = r.hole.recycle;
      const drop = shouldDropBug(bug, zone);
      if (drop && impact.hit === 'ground') return 'single';
      if (impact.hit === 'out' || (c && score >= 400 && dist(impact.x, impact.y, c.x, c.y) > 65)) return 'dig';
      return 'single';
    }
    if (impact.hit === 'out') return 'dig';
    let target = impact.object;
    let nearest = Infinity;
    for (const o of r.objects) {
      if (!active(o) || !['token', 'bigToken', 'pushToken'].includes(o.type)) continue;
      const c = objectCenter(o), d = dist(impact.x, impact.y, c.x, c.y);
      if (d < nearest) { nearest = d; if (!target || !['token', 'bigToken', 'pushToken'].includes(target.type)) target = o; }
    }
    const directTarget = target && ['token', 'bigToken', 'pushToken'].includes(target.type) && impact.object === target;
    // Hostage risk may raise the score of a useful shot; do not accidentally turn it into a dig.
    if (!directTarget && nearest > 31) return 'dig';
    if (!target) return 'dig';
    const hp = target.hp ?? 1;
    if (target.type === 'bigToken' && hp > 120) {
      const singles = Math.ceil(hp / 120), bursts = Math.ceil(hp / 195);
      if (bursts < singles) return 'burst';
    }
    // A pushToken moves after the first burst projectile, so the later rounds are unreliable.
    return 'single';
  }

  decide(r) {
    this.turn++;
    const terrain = decodeTerrain(r.terrain);
    if (this.lastPlan && r.lastResult?.impacts?.length) {
      const actual = r.lastResult.impacts[0];
      const predicted = this.lastPlan.impact;
      const error = dist(actual.x, actual.y, predicted.x, predicted.y);
      const noChanges = !r.lastResult.objectChanges?.length;
      const wentOut = r.lastResult.impacts.some(i => i.hit === 'out');
      if (noChanges && (wentOut || error > 80)) {
        this.failedActions.push({ ...this.lastPlan.action });
        if (this.failedActions.length > 10) this.failedActions.shift();
      }
      if (this.lastPlan.action.kind !== 'spread' && error < 180 && predicted.hit === 'ground' && actual.objectId == null && actual.hit !== 'out') {
        this.biasX = clamp(this.biasX * 0.5 + (actual.x - predicted.x) * 0.5, -80, 80);
        this.biasY = clamp(this.biasY * 0.5 + (actual.y - predicted.y) * 0.5, -40, 40);
      } else {
        this.biasX *= 0.5;
        this.biasY *= 0.5;
      }
    }
    const tokens = r.objects.some(o => active(o) && ['token', 'bigToken', 'pushToken'].includes(o.type));
    const bug = r.objects.some(o => active(o) && o.type === 'bug');
    const needsTokens = r.hole.goal === 'tokens' || r.hole.goal === 'both';
    const needsBug = r.hole.goal === 'bug' || r.hole.goal === 'both';
    // Tokens can also be mere obstacles in a bug-only hole.
    const mode = needsTokens && tokens ? 'tokens' : needsBug && bug ? 'bug' : tokens ? 'tokens' : 'bug';
    const stands = this.stands(r, terrain);
    const started = performance.now();
    let best = null;
    const consider = (stand, angle, power, dir) => {
      const attack = { kind: 'single', angle, power, dir };
      const impact = this.simulate(r, terrain, stand, attack);
      const raw = mode === 'tokens' ? this.tokenScore(r, terrain, impact) : this.bugScore(r, terrain, impact);
      const repeated = this.failedActions.some(a => a.dir === dir && Math.abs(a.angle - angle) <= 2 && Math.abs(a.power - power) <= 3);
      const score = raw + power * 0.0005 + (stand.move ? 0.02 : 0) + (repeated ? 2000 : 0);
      if (!best || score < best.score) best = { stand, attack, impact, score, raw };
    };
    outer: for (const stand of stands) {
      for (const dir of [1, -1]) {
        for (let angle = 4; angle <= 85; angle += 3) {
          for (let power = 6; power <= 100; power += 4) {
            consider(stand, angle, power, dir);
            if (performance.now() - started > 610) break outer;
          }
        }
      }
    }
    if (!best) {
      const stand = stands[0];
      best = { stand, attack: { kind: 'single', angle: 45, power: 55, dir: 1 } };
      best.impact = this.simulate(r, terrain, stand, best.attack);
      best.raw = 1e6;
    }
    const seed = { ...best.attack };
    const baseAngle = seed.angle, basePower = seed.power;
    for (let angle = Math.max(0, baseAngle - 3); angle <= Math.min(85, baseAngle + 3); angle += 1) {
      for (let power = Math.max(1, basePower - 4); power <= Math.min(100, basePower + 4); power += 1) {
        if (performance.now() - started > 820) break;
        consider(best.stand, angle, power, seed.dir);
      }
    }
    best.attack.kind = this.chooseKind(r, best.impact, best.raw, mode);

    // Spread is worthwhile only for a cluster of fragile ordinary tokens.
    if (mode === 'tokens' && best.raw < 30) {
      const fragile = r.objects.filter(o => active(o) && o.type === 'token');
      if (fragile.length >= 2 && best.attack.kind === 'single') {
        const hit = new Set();
        for (const offset of [-6, 0, 6]) {
          const im = this.simulate(r, terrain, best.stand, best.attack, offset);
          if (im.object?.type === 'token') hit.add(im.object.id);
          for (const o of fragile) {
            const c = objectCenter(o);
            if (im.hit === 'ground' && dist(im.x, im.y, c.x, c.y) < 27) hit.add(o.id);
          }
        }
        if (hit.size >= 2) best.attack.kind = 'spread';
      }
    }
    best.attack.angle = Math.round(clamp(best.attack.angle, 0, 85) * 100) / 100;
    best.attack.power = Math.round(clamp(best.attack.power, 0, 100) * 100) / 100;
    this.lastPlan = { impact: best.impact, action: best.attack, mode };
    const reply = { attack: best.attack };
    if (best.stand.move && r.hole.teeActive) reply.moveX = Math.round(best.stand.x * 100) / 100;
    process.stderr.write(`洞 ${r.hole.id} 第${r.strokes + 1}杆 ${mode} ${best.attack.kind} 角${best.attack.angle} 力${best.attack.power} 向${best.attack.dir} 预测${best.impact.hit}(${best.impact.x.toFixed(0)},${best.impact.y.toFixed(0)}) 分${best.raw.toFixed(1)}\n`);
    return reply;
  }
}

let bot = null;
for await (const line of createInterface({ input: process.stdin })) {
  try {
    const r = JSON.parse(line);
    let reply;
    if (r.type === 'exam') {
      reply = { predictions: r.questions.map(q => examPrediction(q, r.ground)) };
    } else if (r.type === 'init') {
      bot = new Bot(r);
      reply = { ok: true };
    } else if (r.type === 'turn') {
      if (!bot) bot = new Bot(r);
      reply = bot.decide(r);
    } else {
      throw new Error(`unknown message ${r.type}`);
    }
    process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
  } catch (e) {
    process.stderr.write(`${e.stack || e}\n`);
    // Keep the protocol valid even if an unusual observation defeats the planner.
    const reqId = (() => { try { return JSON.parse(line).reqId; } catch { return 0; } })();
    process.stdout.write(JSON.stringify({ reqId, attack: { kind: 'single', angle: 45, power: 50, dir: 1 } }) + '\n');
  }
}
