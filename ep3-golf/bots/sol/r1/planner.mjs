import { performance } from 'node:perf_hooks';
import { aimVelocity, clamp, DEG, muzzle, Path, setPosition, Terrain, trace, VELOCITY } from './geometry.mjs';
import { advanceWorld, alive, applyImpact, blastEffect, distanceToBox, motionVariant, movementDefaults, objectFloor, objectList, standOptions, station, token, wanted } from './world.mjs';

const attackKinds = ['single', 'dig', 'burst', 'spread'];
const angleTimes = [0, 0.027, 0.06, 0.105, 0.16, 0.225, 0.30, 0.38, 0.46, 0.54, 0.62, 0.70, 0.78, 0.84, 0.895, 0.94, 0.975, 1];
const shortTimes = [0.02, 0.11, 0.24, 0.40, 0.57, 0.72, 0.85, 0.94, 0.985];
const report = process.env.GOLF_DEBUG === '1';

function relevantObjects(env) {
  return env.objects.filter(o => wanted(o, env.hole.goal));
}

function directionToBox(o, box) {
  if (!box) return 0;
  if (o.x <= box.x0) return 1;
  if (o.x >= box.x1) return -1;
  return 0;
}

function targetSeeds(env, reduced = false) {
  const result = [];
  const add = (x, y, target, kind) => {
    if (Number.isFinite(y) && x > 1 && x < env.terrain.w - 1) result.push({ x, y, target, kind });
  };
  const goal = relevantObjects(env);
  for (const o of goal) {
    add(o.x, o.cy, o, 'center');
    if (reduced) {
      if (o.type === 'bug') {
        const dir = directionToBox(o, env.box);
        for (const dx of dir ? [-dir * 28, -dir * 48] : [-18, 18]) {
          add(o.x + dx, env.terrain.surface(o.x + dx, o.y - 7, o.y + 110) + 0.35, o, 'support');
        }
      }
      continue;
    }
    add(o.x, o.top + 3, o, 'edge');
    add(o.x, o.bottom - 3, o, 'edge');
    if (o.type === 'bug' || o.type === 'pushToken') {
      const dir = o.type === 'bug' ? directionToBox(o, env.box) || 1 : 1;
      add(o.x - dir * o.hw * 0.7, o.cy, o, 'edge');
      add(o.x + dir * o.hw * 0.7, o.cy, o, 'edge');
      add(o.x - dir * 9, o.cy + 7, o, 'edge');
      add(o.x + dir * 9, o.cy - 7, o, 'edge');
      const offsets = o.type === 'bug'
        ? [-59, -54, -49, -44, -38, -32, -28, -24, -19, -13, -7, 0, 7, 13, 19, 24, 28, 32, 38, 44, 49, 54, 59]
        : [-52, -34, -24, -16, -8, 0, 8, 16, 24, 34, 52];
      for (const dx of offsets) {
        let y = env.terrain.surface(o.x + dx, o.y - 7, o.y + 180);
        add(o.x + dx, y + 0.35, o, 'support');
        if (Math.abs(dx) <= 19 && y > o.y + 10) {
          y = env.terrain.surface(o.x + dx, o.top - 2, o.y - 7);
          add(o.x + dx, y + 0.35, o, 'support');
        }
      }
    } else {
      for (const dx of [-o.hw - 5, 0, o.hw + 5]) {
        const y = env.terrain.surface(o.x + dx, o.y - 9, o.y + 130);
        add(o.x + dx, y + 2, o, 'support');
      }
      if (o.type === 'bigToken') {
        add(o.x - o.hw + 1, o.cy, o, 'edge');
        add(o.x + o.hw - 1, o.cy, o, 'edge');
      }
    }
  }
  if (!reduced) {
    for (let i = 0; i < goal.length; i++) {
      for (let j = i + 1; j < goal.length; j++) {
        const a = goal[i], b = goal[j];
        if (Math.hypot(a.x - b.x, a.cy - b.cy) > 90) continue;
        const tx = (a.x + b.x) / 2;
        const ty = (a.cy + b.cy) / 2;
        add(tx, ty, a, 'pair');
        add(tx, env.terrain.surface(tx, Math.max(a.y, b.y) - 15) + 2, a, 'pair');
      }
    }
  }
  return result.sort((a, b) => Number(b.kind === 'center') - Number(a.kind === 'center'));
}

function actionFor(from, actor, target, t, wind, fly = false) {
  const velocity = aimVelocity(from, target.x, target.y, t, wind);
  const power = Math.hypot(velocity.vx, velocity.vy) / VELOCITY;
  if (power > 100.00001) return [];
  const dirs = velocity.vx > 0 ? [1, -1] : [-1, 1];
  const results = [];
  for (const dir of dirs) {
    const realAngle = Math.atan2(-velocity.vy, dir * velocity.vx) / DEG;
    const angle = realAngle - (fly ? 0 : dir * actor.groundAngle);
    if (angle < -0.00001 || angle > (fly ? 90 : 85) + 0.00001) continue;
    results.push({ dir, angle: clamp(angle, 0, fly ? 90 : 85), power: clamp(power, 0, 100) });
  }
  return results;
}

function launch(s, from, actor, wind, offset = 0, fly = false) {
  return Path.shot(from, s.angle + offset + (fly ? 0 : s.dir * actor.groundAngle), s.power, s.dir, wind);
}

function timeBounds(from, seed, wind) {
  const dx = Math.abs(seed.x - from.x);
  const dy = seed.y - from.y;
  const maxSpeed = 100 * VELOCITY;
  const maxT = maxSpeed + Math.sqrt(Math.max(0, maxSpeed * maxSpeed + 2 * dy));
  const min = Math.max(0.55, Math.hypot(dx, dy) / (maxSpeed + Math.abs(wind) * 1.4 + 1) * 0.87);
  return [min, Math.max(min, maxT)];
}

function fastSingleScore(env, hit, seed, aimTime) {
  if (hit.type === 'out') return -4;
  const { terrain, objects, hole, box, movement, attacks } = env;
  let score = 0;
  for (const o of objects) {
    const distX = Math.abs(hit.x - o.x);
    if (distX > 62 || Math.abs(hit.y - o.cy) > 85) continue;
    const effect = blastEffect(terrain, hit, o, attacks.single, movement);
    if (o.type === 'chicken') {
      if (effect?.damage) score -= 1.45;
      else if (hit.type === 'ground' && Math.hypot(distX, hit.y - o.y) < 31) score -= 0.4;
    } else if (wanted(o, hole.goal)) {
      if (o.type === 'bug') {
        if (effect) {
          const oldDist = distanceToBox(o.x, o.cy, box);
          const newDist = distanceToBox(o.x + effect.travel, o.cy, box);
          score += clamp((oldDist - newDist) / 85, -0.5, 1.15);
          if (newDist < 0.5) score += 0.55;
        }
        if (Math.hypot(distX, hit.y - o.y) < 59) {
          score += 0.19;
          const dig = blastEffect(terrain, hit, o, attacks.dig, movement);
          if (dig) {
            const oldDist = distanceToBox(o.x, o.cy, box);
            const newDist = distanceToBox(o.x + dig.travel, o.cy, box);
            score += 0.39 * clamp((oldDist - newDist) / 100, 0, 1);
          }
        }
      } else {
        if (effect?.damage) score += Math.min(0.98, 0.84 * effect.damage / Math.min(120, o.hp ?? 1));
        if (Math.hypot(distX, hit.y - o.y) < 49) score += 0.22;
      }
    }
  }
  if (seed.target && hit.type === 'ground' && hit.t <= aimTime + 2) {
    const offset = Math.hypot(seed.target.x - hit.x, seed.target.cy - hit.y);
    score += 0.28 / (1 + offset / 85);
    if (offset < 70) score += 0.15;
  }
  return score;
}

function candidateKey(q) {
  return [
    q.hit.type,
    q.hit.object?.id ?? '',
    Math.floor(q.hit.x / (q.seed.target?.type === 'bug' ? 4 : 8)),
    Math.floor(q.hit.y / 7),
    Math.floor(q.hit.t / 12),
    q.s.dir,
    Math.floor(q.from.x / 92),
  ].join('/');
}

function storeCandidate(table, q) {
  const key = candidateKey(q);
  const old = table.get(key);
  if (!old || old.fast < q.fast) table.set(key, q);
}

function roughCandidates(env, poses, seeds, deadline, dense = false) {
  const table = new Map();
  let operations = 0;
  for (let index = 0; index < seeds.length; index++) {
    const seed = seeds[index];
    for (const pose of poses) {
      const from = pose.from;
      const [min, max] = timeBounds(from, seed, env.wind);
      const times = dense ? denseTimes : seed.kind === 'center' || seed.kind === 'support' ? angleTimes : shortTimes;
      for (const fraction of times) {
        if (((operations++) & 63) === 0 && performance.now() > deadline) return [...table.values()];
        const t = min + (max - min) * fraction;
        for (const s of actionFor(from, pose.actor, seed, t, env.wind)) {
          const path = launch(s, from, pose.actor, env.wind);
          const hit = trace(path, env.terrain, env.objects);
          if (hit.type === 'out') continue;
          let fast = fastSingleScore(env, hit, seed, t);
          fast -= pose.risk + 0.006 * t / 35 + (pose.moveX === undefined ? 0 : 0.009);
          if (path.peakY < -80) fast -= 0.06;
          storeCandidate(table, { s, path, hit, seed, time: t, from, pose, fast });
        }
      }
    }
  }
  return [...table.values()];
}

const denseTimes = Array.from({ length: 101 }, (_, i) => i / 100);

function simulatedAttack(env, q, kind, first) {
  const state = {
    terrain: env.terrain,
    objects: env.objects.map(o => ({ ...o, preShotX: o.x, preShotY: o.y })),
    edited: false,
    penalties: 0,
    bugOut: false,
    impacts: [],
  };
  const { attacks, wind } = env;
  const spec = attacks[kind];
  if (kind === 'burst') {
    for (let i = 0; i < 3; i++) {
      const hit = i === 0 ? first : trace(q.path, state.terrain, state.objects, 0, { step: 1.2 });
      state.impacts.push(hit);
      applyImpact(state, hit, spec, env, 0);
      if (i < 2) advanceWorld(state, env, 48);
    }
    advanceWorld(state, env);
  } else if (kind === 'spread') {
    const paths = [
      launch(q.s, q.from, q.pose.actor, wind, -6), q.path, launch(q.s, q.from, q.pose.actor, wind, 6),
    ];
    const active = [true, true, true];
    let t = 0, ticks = 0, spent = 0;
    while (spent < 3 && t < 116) {
      const end = state.objects.some(o => o.moving && alive(o)) ? Math.min(116, t + 2.25) : 116;
      let n = -1;
      let hit = null;
      for (let j = 0; j < paths.length; j++) {
        if (!active[j]) continue;
        const collision = trace(paths[j], state.terrain, state.objects, t, { step: 1.2, end });
        if (collision.type === 'flying') continue;
        if (!hit || collision.t < hit.t) {
          hit = collision;
          n = j;
        }
      }
      const next = hit?.t ?? end;
      ticks += Math.max(0, next - t) * 1.72;
      if (ticks >= 1) {
        advanceWorld(state, env, Math.floor(ticks));
        ticks %= 1;
      }
      if (hit) {
        active[n] = false;
        spent++;
        state.impacts.push(hit);
        applyImpact(state, hit, spec, env, 0);
      }
      t = next + 0.001;
    }
    advanceWorld(state, env);
  } else {
    state.impacts.push(first);
    applyImpact(state, first, spec, env);
  }
  return state;
}

function terrainCredit(env, q, state, kind) {
  if (!state.edited || q.hit.type !== 'ground' || !q.seed.target ||
    !state.objects.some(o => o.id === q.seed.target.id && wanted(o, env.hole.goal))) return 0;
  if (q.hit.t > q.time + 0.8 || q.seed.kind === 'support') return 0;
  const next = trace(q.path, state.terrain, state.objects);
  const oldDist = Math.hypot(q.hit.x - q.seed.x, q.hit.y - q.seed.y);
  if (next.object?.id === q.seed.target.id) return kind === 'burst' ? 0.42 : 0.57;
  const gain = next.t - q.hit.t;
  if (gain < 0.15) return 0;
  const reach = Math.hypot(next.x - q.hit.x, next.y - q.hit.y);
  const newDist = Math.hypot(next.x - q.seed.x, next.y - q.seed.y);
  return clamp((oldDist - newDist + Math.min(30, reach) * 0.35) / (95 + oldDist * 0.8), 0.005, 0.42);
}

function scoreResult(env, q, state, kind) {
  const { hole, box, terrain, config } = env;
  let gain = 0, remaining = 0, targetProgress = 0;
  for (let i = 0; i < env.objects.length; i++) {
    const o = env.objects[i];
    if (!wanted(o, hole.goal)) continue;
    const after = state.objects[i];
    if (wanted(after, hole.goal)) remaining++;
    if (token(o)) {
      const hp = o.hp ?? 1;
      const fraction = alive(after) ? clamp((hp - (after.hp ?? hp)) / hp, 0, 1) : 1;
      const damageCredit = Math.min(1.32, 0.55 * (hp * fraction / 180) ** 0.85);
      gain += fraction * 0.66 + damageCredit + (fraction >= 1 ? 0.42 : 0);
      targetProgress += fraction;
      if (o.type === 'pushToken' && alive(after) && after.y > o.y + 20) gain += 0.035;
    } else {
      const start = distanceToBox(o.x, o.cy, box);
      const end = distanceToBox(after.x, after.cy, box);
      if (after.inRecycle) {
        const margin = Math.min(after.x - box.x0, box.x1 - after.x, after.cy - box.y0, box.y1 - after.cy);
        gain += 1.37 + clamp(start / 420, 0, 0.72) + clamp(margin / 85, 0, 0.26);
        if (Math.abs(after.x - o.x) > 13 && margin < 8) gain -= 0.19;
        targetProgress += 1;
      } else if (!state.bugOut) {
        gain += clamp((start - end) / 92, -1.35, 1.27);
        targetProgress += Math.max(0, (start - end) / 140);
        if (after.x < 25 || after.x > terrain.w - 25) gain -= 0.30;
        if (end >= start - 7 && Math.hypot(after.x - o.x, after.y - o.y) > 12 &&
          env.history.slice(-4).some(entry => entry.bugBefore &&
            Math.hypot(entry.bugBefore.x - after.x, entry.bugBefore.y - after.y) < 13)) gain -= 0.22;
      }
    }
  }
  const room = hole.cap - env.obs.strokes;
  const penaltyCost = room <= 3 ? 1.34 : 1.17;
  gain -= state.penalties * penaltyCost;
  if (state.bugOut) gain -= 0.16;
  if (!remaining) gain += (env.obs.strokes >= hole.par + 2 ? state.penalties * penaltyCost : state.penalties * 0.26) + 0.78;
  if (state.penalties + 1 >= room && remaining) gain -= 0.7;
  if (room === 1 && remaining) gain -= 0.36;

  if (remaining && targetProgress < 1.25) gain += terrainCredit(env, q, state, kind) * (1 - targetProgress * 0.57);
  if (state.edited) {
    const a = q.pose.actor;
    const feet = a.y + config.BODY.footOffset;
    if (!state.terrain.solid(a.x, feet + 1)) gain -= hole.teeActive ? 0.05 : 0.26;
  }
  for (const o of env.objects) {
    if (o.type !== 'chicken' || !alive(o) || !alive(state.objects.find(a => a.id === o.id))) continue;
    if (state.impacts.some(i => i.type !== 'out' && Math.hypot(i.x - o.x, i.y - o.cy) < 32 &&
        terrain.visible(i.x, i.y, o.x, o.cy))) gain -= 0.18;
  }
  const repeated = env.history.filter(entry => entry.noProgress &&
    Math.abs(entry.x - q.hit.x) < 9 && Math.abs(entry.y - q.hit.y) < 9 && entry.kind === kind);
  gain -= repeated.length * 0.19;
  gain -= q.pose.risk + (q.pose.moveX === undefined ? 0 : 0.012) + q.hit.t * 0.0006;
  if (q.path.peakY < -55) gain -= Math.min(0.18, (-55 - q.path.peakY) / 950);
  const end = state.impacts.at(-1);
  if (end?.type === 'out' && kind !== 'single' && kind !== 'dig') gain -= 0.013;
  return { score: gain, remaining, targetProgress };
}

function rankedCandidates(rough) {
  rough.sort((a, b) => b.fast - a.fast);
  const primary = rough.slice(0, 76);
  const seen = new Set(primary);
  const perObject = new Map();
  const perHit = new Map();
  for (const q of rough) {
    if (seen.has(q)) continue;
    const id = q.seed.target?.id;
    const amount = perObject.get(id) ?? 0;
    const key = [q.hit.object?.id, q.hit.x / 20 | 0, q.hit.y / 12 | 0].join('/');
    const local = perHit.get(key) ?? 0;
    if (amount >= 20 || local >= 2) continue;
    primary.push(q);
    perObject.set(id, amount + 1);
    perHit.set(key, local + 1);
    if (primary.length >= 176) break;
  }
  return primary;
}

function evaluate(env, candidates, end, old, selectedKinds = attackKinds) {
  let best = old;
  const pool = new Map();
  for (const q of candidates) {
    if (performance.now() >= end) break;
    const impact = trace(q.path, env.terrain, env.objects, 0, { step: 0.72 });
    for (const kind of selectedKinds) {
      if (!env.attacks[kind] || (impact.type === 'out' && kind !== 'spread')) continue;
      const state = simulatedAttack(env, q, kind, impact);
      const result = scoreResult(env, q, state, kind);
      const verdict = {
        ...result, q, kind,
        action: { ...(q.pose.moveX === undefined ? {} : { moveX: q.pose.moveX }), attack: { kind, ...q.s } },
        impacts: state.impacts,
        objects: state.objects,
        penalties: state.penalties,
      };
      if (!best || verdict.score > best.score) best = verdict;
      const bug = state.objects.find(o => o.type === 'bug');
      const key = [kind, impact.object?.id ?? 'g', impact.x / 7 | 0, impact.y / 6 | 0,
        bug ? bug.x / 26 | 0 : '', bug ? bug.y / 18 | 0 : '', q.from.x / 140 | 0].join('/');
      if (!pool.has(key) || pool.get(key).score < verdict.score) pool.set(key, verdict);
    }
  }
  const shortlist = [...pool.values()].sort((a, b) => b.score - a.score);
  return { best, shortlist };
}

function trajectoryClearance(path, impact) {
  const o = impact.object;
  if (!o) return Infinity;
  let clearance = 0;
  const speed = Math.hypot(path.x(impact.t + 0.25) - impact.x, path.y(impact.t + 0.25) - impact.y) * 4;
  const end = Math.min(5, 2.6 * o.hw / Math.max(1, speed));
  for (let i = 1; i <= 8; i++) {
    const t = impact.t + end * i / 8;
    const x = path.x(t), y = path.y(t);
    const depth = o.r ? o.r - Math.hypot(x - o.x, y - o.cy)
      : Math.min(x - o.left, o.right - x, y - o.top, o.bottom - y);
    clearance = Math.max(clearance, depth);
  }
  return clearance;
}

function unevenBetween(terrain, bug, x, y) {
  if (Math.abs(y - bug.y) > 7) return true;
  const left = Math.min(bug.x, x), right = Math.max(bug.x, x);
  for (let p = left; p <= right; p += 16) {
    if (!terrain.solid(p, bug.y + 2) || terrain.solid(p, bug.y - 9)) return true;
  }
  return false;
}

function robustPick(env, shortlist, deadline) {
  const bug = env.objects.find(o => o.type === 'bug' && alive(o));
  const slider = env.objects.find(o => o.type === 'pushToken' && alive(o));
  const unique = new Set();
  let best = null, count = 0;
  const alternatives = [];
  shortlist.sort((a, b) => b.score - a.score);
  for (const verdict of shortlist) {
    if (count >= 39 || performance.now() >= deadline) break;
    if (best && verdict.score < best.robust - 0.46) break;
    const { q, kind } = verdict;
    const hit = verdict.impacts[0];
    const dest = verdict.objects.find(o => o.id === bug?.id);
    const key = [kind, hit?.x / 5 | 0, hit?.y / 5 | 0, dest?.x / 17 | 0,
      dest?.y / 8 | 0, q.pose.actor.groundAngle / 5 | 0].join('/');
    if (unique.has(key)) continue;
    unique.add(key);
    count++;
    const near = verdict.impacts.some(hit => bug && Math.hypot(hit.x - bug.x, hit.y - bug.y) < env.attacks[kind].radius + 16);
    const onGrade = near && unevenBetween(env.terrain, bug, dest?.x ?? bug.x, dest?.y ?? bug.y);
    const followingEdge = onGrade && verdict.impacts.some(i => i.y > bug.cy - 2 &&
      (i.object?.id === bug.id || (i.type === 'ground' && Math.hypot(i.x - bug.x, i.y - bug.y) < env.attacks[kind].radius)));
    const sliderNear = slider && verdict.impacts.some(hit => hit.object?.id === slider.id || Math.hypot(hit.x - slider.x, hit.y - slider.cy) < 37);
    const thin = trajectoryClearance(q.path, q.hit) < 1.75;
    let robust = verdict.score;
    const outcomes = [verdict];
    if (near || (sliderNear && ['burst', 'spread'].includes(kind)) || thin) {
      for (const sign of [-1, 1]) {
        const movement = motionVariant(env.movement, onGrade, followingEdge, sign);
        const shift = (0.36 + 0.00064 * Math.abs((hit?.x ?? q.from.x) - q.from.x)) * sign;
        const from = { x: q.from.x + shift, y: q.from.y + 0.28 * sign };
        const path = launch(q.s, from, q.pose.actor, env.wind);
        const altered = { ...q, from, path };
        const collision = trace(path, env.terrain, env.objects, 0, { step: 0.72 });
        const cross = { ...env, movement };
        const sim = simulatedAttack(cross, altered, kind, collision);
        const estimate = scoreResult(cross, altered, sim, kind);
        outcomes.push({ score: estimate.score, remaining: estimate.remaining, objects: sim.objects, penalties: sim.penalties });
      }
      const nominalWeight = onGrade ? 0.38 : 0.50;
      robust = nominalWeight * verdict.score + (1 - nominalWeight) / 2 * (outcomes[1].score + outcomes[2].score);
      if (near && outcomes.some(o => o.penalties > verdict.penalties)) robust -= 0.14;
      if (followingEdge) robust -= kind === 'spread' || kind === 'burst' ? 0.29 : 0.15;
    }
    const candidate = { ...verdict, robust, outcomes };
    alternatives.push(candidate);
    if (!best || robust > best.robust) best = candidate;
  }
  return best && { ...best, alternatives: alternatives.sort((a, b) => b.robust - a.robust) };
}

function variations(env, shortlist) {
  const list = [];
  const seen = new Set();
  for (const { q, kind } of shortlist) {
    for (const [a, p] of [[-2, 0], [2, 0], [0, -2], [0, 2], [-0.6, -1], [0.6, 1], [-6, 0], [6, 0]]) {
      if (Math.abs(a) === 6 && kind !== 'spread') continue;
      const s = { ...q.s, angle: clamp(q.s.angle + a, 0, 85), power: clamp(q.s.power + p, 0, 100) };
      const key = [q.from.x, Math.round(s.angle * 3), Math.round(s.power * 3), s.dir].join('/');
      if (seen.has(key)) continue;
      seen.add(key);
      const path = launch(s, q.from, q.pose.actor, env.wind);
      const hit = trace(path, env.terrain, env.objects);
      if (hit.type === 'out') continue;
      list.push({ ...q, s, path, hit });
    }
  }
  return list;
}

function wingCandidates(env, rough) {
  const seen = new Set();
  const result = [];
  for (const q of rough) {
    if (result.length >= 60) break;
    if (q.hit.type !== 'object' || !wanted(q.hit.object, env.hole.goal)) continue;
    const key = [q.hit.object.id, q.pose.actor.x / 45 | 0, q.hit.t / 8 | 0].join('/');
    if (seen.has(key)) continue;
    seen.add(key);
    for (const shift of [-6, 6]) {
      const angle = q.s.angle + shift;
      if (angle < 0 || angle > 85) continue;
      const s = { ...q.s, angle };
      const path = launch(s, q.from, q.pose.actor, env.wind);
      const hit = trace(path, env.terrain, env.objects);
      result.push({ ...q, s, path, hit });
    }
  }
  return result;
}

function selectPoses(poses, origin) {
  const sorted = poses.slice().sort((a, b) => a.actor.x - b.actor.x);
  return [...new Set([poses[0], sorted[0], sorted.at(-1),
    sorted.reduce((best, pose) => Math.abs(pose.actor.x - origin) < Math.abs(best.actor.x - origin) ? pose : best)])];
}

function apertureTargets(env, options) {
  const remains = [];
  const planned = new Map();
  for (const option of options) {
    if (option.penalties > 0) continue;
    for (const o of option.objects) {
      if (o.type !== 'bigToken' && o.type !== 'pushToken') continue;
      const hp = alive(o) ? o.hp : 0;
      planned.set(o.id, Math.min(planned.get(o.id) ?? Infinity, hp));
    }
  }
  for (const o of relevantObjects(env)) {
    if (o.type !== 'bigToken' && o.type !== 'pushToken') continue;
    if (o.type === 'pushToken' && o.hp > 185) continue;
    const minimum = planned.get(o.id);
    const potential = Math.min(o.hp, env.attacks.burst.damage * 3);
    if (minimum === undefined || o.hp - minimum < potential - 14) remains.push(o);
  }
  return remains.slice(0, 2).flatMap(o => [o.top + 2, o.top + o.hh * 0.49, o.cy, o.cy + o.hh * 0.48, o.bottom - 2]
    .map(y => ({ x: o.x, y, target: o, kind: 'center' })));
}

function lookahead(env, candidates, poses, deadline) {
  const start = candidates[0];
  if (!start || env.hole.cap - env.obs.strokes <= 1 || start.outcomes.every(o => !o.remaining && !o.penalties)) return start;
  const tried = new Set();
  let best = start, bestValue = -Infinity;
  for (const candidate of candidates) {
    if (tried.size >= 5 || performance.now() > deadline - 9 || candidate.robust < start.robust - 0.25) break;
    const { q, kind } = candidate;
    const spot = candidate.impacts[0];
    const key = [kind, spot?.x / 14 | 0, spot?.y / 10 | 0, candidate.remaining].join('/');
    if (tried.has(key)) continue;
    tried.add(key);
    if (!candidate.remaining) {
      if (candidate.robust + 0.13 > bestValue) {
        best = candidate;
        bestValue = candidate.robust + 0.13;
      }
      continue;
    }
    const from = q.pose.actor;
    const impact = trace(q.path, env.terrain, env.objects, 0, { step: 0.72 });
    const state = simulatedAttack(env, q, kind, impact);
    const after = { ...env, terrain: state.terrain, objects: state.objects,
      obs: { ...env.obs, strokes: env.obs.strokes + 1 + state.penalties, actor: from } };
    const newPoses = selectPoses(poses, from.x).filter(pose => after.terrain.solid(pose.actor.x, pose.actor.y + env.config.BODY.footOffset + 2));
    const shortlist = roughCandidates(after, newPoses, targetSeeds(after, true), Math.min(deadline - 5, performance.now() + 13));
    shortlist.sort((a, b) => b.fast - a.fast);
    const follow = evaluate(after, shortlist.slice(0, 9), Math.min(deadline, performance.now() + 10)).best;
    const adjustment = follow ? 0.25 * clamp(follow.score - 0.72, -0.50, 0.85) : -0.10;
    const value = candidate.robust + adjustment;
    if (value > bestValue) {
      best = candidate;
      bestValue = value;
    }
  }
  return best;
}

function flyStations(env) {
  const positions = new Set();
  for (const o of relevantObjects(env)) {
    for (const dx of [-340, -225, -110, 80, 180, 290, 420]) {
      positions.add(clamp(Math.round((o.x + dx) / 16) * 16, 18, env.terrain.w - 18));
    }
  }
  for (let x = 60; x < env.terrain.w - 15; x += 90) positions.add(x);
  return [...positions].map(x => station(env.terrain, x, env.config)).filter(Boolean);
}

function findFlight(env, best, end) {
  if (!env.obs.fly || env.hole.cap - env.obs.strokes <= 1 || best?.remaining === 0 || (best?.score ?? 0) > 0.67) return null;
  const from = muzzle(env.obs.actor, env.config.BODY);
  const landings = new Map();
  for (const actor of flyStations(env)) {
    if (performance.now() >= end - 35) break;
    const tx = actor.x;
    const ty = actor.y + env.config.BODY.footOffset + 0.5;
    if (Math.abs(tx - env.obs.actor.x) < 38) continue;
    const [min, max] = timeBounds(from, { x: tx, y: ty }, env.wind);
    for (const fraction of [0.10, 0.29, 0.51, 0.7, 0.91, 0.99]) {
      const time = min + (max - min) * fraction;
      for (const s of actionFor(from, env.obs.actor, { x: tx, y: ty }, time, env.wind, true)) {
        const path = launch(s, from, env.obs.actor, env.wind, 0, true);
        const hit = trace(path, env.terrain, []);
        if (hit.type !== 'ground' || Math.abs(hit.x - tx) > 20 || Math.abs(hit.y - ty) > 14) continue;
        const landing = station(env.terrain, hit.x, env.config);
        if (!landing || Math.abs(landing.y + env.config.BODY.footOffset - hit.y) > 5) continue;
        if (!env.terrain.solid(hit.x - 7, hit.y + 8) || !env.terrain.solid(hit.x + 7, hit.y + 8)) continue;
        const key = Math.round(hit.x / 30);
        const priority = -Math.abs(landing.groundAngle) * 0.004 - time * 0.0008;
        if (landings.get(key)?.priority >= priority) continue;
        landings.set(key, { actor: landing, s, priority });
      }
    }
  }
  const seeds = targetSeeds(env, true);
  let chosen = null;
  for (const landing of landings.values()) {
    if (performance.now() >= end) break;
    const pose = { actor: landing.actor, from: muzzle(landing.actor, env.config.BODY), risk: 0 };
    const after = { ...env, hole: { ...env.hole, teeActive: false }, obs: { ...env.obs, strokes: env.obs.strokes + 1, actor: landing.actor } };
    const trial = roughCandidates(after, [pose], seeds, Math.min(end, performance.now() + 7));
    trial.sort((a, b) => b.fast - a.fast);
    const follow = evaluate(after, trial.slice(0, 8), Math.min(end, performance.now() + 11)).best;
    if (!follow) continue;
    const value = follow.score + landing.priority - 0.75;
    if (value <= (chosen?.value ?? Math.max(0.02, (best?.score ?? 0) + 0.08))) continue;
    chosen = { action: { fly: landing.s }, value };
  }
  return chosen;
}

function unplayable(env, best, initial) {
  if (!initial || !env.box || env.hole.cap - env.obs.strokes < 3 || (best?.score ?? 0) > 0.19) return false;
  const bug = relevantObjects(env).find(o => o.type === 'bug');
  if (!bug) return false;
  const start = distanceToBox(initial.x, initial.cy, env.box);
  const current = distanceToBox(bug.x, bug.cy, env.box);
  return current - start > 80 && Math.abs(initial.x - bug.x) > 50 &&
    env.terrain.solid(initial.x, initial.y + 1) && !env.terrain.solid(initial.x, initial.cy);
}

export class Planner {
  constructor(config, initial) {
    this.config = config;
    this.initialBug = objectList(initial, config).find(o => o.type === 'bug');
    this.history = [];
    this.stands = new Map();
    this.previous = null;
    this.previousTerrain = null;
    this.previousObjects = [];
    this.gains = { bug: 1, pushToken: 1 };
    this.ceiling = -Infinity;
  }

  update(obs) {
    const last = obs.lastResult;
    const previous = this.previous;
    if (!last || !previous?.action?.attack) return;
    const actual = last.impacts?.[0];
    const predicted = previous.impacts?.[0];
    const box = obs.hole.recycle;
    const progressed = (last.objectChanges ?? []).some(change => {
      if (change.after?.broken || change.after?.inRecycle || change.damage > 0) return true;
      if (!box || change.before?.type !== 'bug' || !change.after) return false;
      const before = distanceToBox(change.before.x, change.before.y - change.before.radius, box);
      const after = distanceToBox(change.after.x, change.after.y - change.after.radius, box);
      return after < before - 4;
    });
    if (actual) {
      const bugBefore = this.previousObjects.find(o => o.type === 'bug' && alive(o));
      this.history.push({ x: actual.x, y: actual.y, kind: previous.kind, noProgress: !progressed, bugBefore });
    }
    if (actual && predicted && Math.hypot(actual.x - predicted.x, actual.y - predicted.y) <= 2.3 &&
        (previous.kind === 'single' || previous.kind === 'dig') && this.previousTerrain && !last.penalties?.length) {
      for (const change of last.objectChanges ?? []) {
        const source = this.previousObjects.find(o => o.id === change.id);
        const expected = previous.objects.find(o => o.id === change.id);
        if (!source || !(source.type in this.gains) || !expected || !change.after) continue;
        const dx = change.after.x - source.x;
        const ideal = expected.x - source.x;
        if (Math.abs(dx) < 18 || Math.abs(ideal) < 18 || dx * ideal <= 0 ||
            unevenBetween(this.previousTerrain, source, change.after.x, change.after.y)) continue;
        const adjustment = clamp(dx / ideal, 0.78, 1.28) ** 0.45;
        this.gains[source.type] = clamp(this.gains[source.type] * adjustment, 0.76, 1.3);
      }
    }
    const nearStand = stand => (last.impacts ?? []).some(i => i.hit !== 'out' &&
      Math.abs(i.x - stand.x) < 83 && Math.abs(i.y - stand.y) < 81);
    for (const [x, stand] of this.stands) if (nearStand(stand)) this.stands.delete(x);
    for (const impact of last.impacts ?? []) {
      if (impact.hit === 'out' && impact.x > 2 && impact.x < this.config.W - 2 && impact.y < -0.1) {
        this.ceiling = Math.max(this.ceiling, impact.y + 0.85);
      }
    }
    if (last.from && Number.isFinite(last.from.x) && Number.isFinite(last.from.y) &&
      Number.isFinite(last.from.groundAngle) && previous.q.pose.moveX !== undefined && !nearStand(last.from)) {
      this.stands.set(previous.q.pose.moveX, { ...last.from, grounded: true });
    }
    if (report && actual && predicted) {
      const dx = (actual.x - predicted.x).toFixed(1);
      const dy = (actual.y - predicted.y).toFixed(1);
      process.stderr.write('落差 ' + dx + ',' + dy + ' 发射 ' + JSON.stringify(last.from ?? null) + '\n');
    }
  }

  turn(obs) {
    const begin = performance.now();
    this.update(obs);
    const terrain = new Terrain(obs.terrain);
    terrain.ceiling = this.ceiling;
    const objects = objectList(obs, this.config);
    for (const penalty of obs.lastResult?.penalties ?? []) {
      if (penalty.reason !== 'bug-out') continue;
      const bug = objects.find(o => o.id === penalty.objectId && alive(o));
      if (!bug) continue;
      const floor = objectFloor(terrain, bug);
      if (floor > bug.y + 0.8 && floor < bug.y + 88) setPosition(bug, bug.x, floor);
    }
    const env = {
      obs, config: this.config, terrain, objects,
      hole: obs.hole, box: obs.hole.recycle, wind: obs.hole.wind,
      movement: {
        bug: { ...movementDefaults.bug, gain: this.gains.bug },
        pushToken: { ...movementDefaults.pushToken, gain: this.gains.pushToken },
      }, attacks: this.config.ATTACKS,
      history: this.history,
    };
    const poses = standOptions(terrain, obs, this.config, this.stands).map(pose => ({
      ...pose, from: muzzle(pose.actor, this.config.BODY),
    }));
    const seeds = targetSeeds(env);
    if (!seeds.length) {
      this.previous = null;
      return { attack: { kind: 'single', angle: 40, power: 55, dir: obs.actor.x < 800 ? 1 : -1 } };
    }
    // Debit the turn for protocol overhead and possible garbage collection.
    const absolute = begin + 740;
    const rough = roughCandidates(env, poses, seeds, begin + 290);
    const primary = rankedCandidates(rough);
    const first = evaluate(env, primary, begin + 435);
    let best = first.best;
    let candidates = first.shortlist.slice(0, 125);
    if (performance.now() < begin + 460 && first.shortlist.length) {
      const more = variations(env, first.shortlist.slice(0, 13));
      const refined = evaluate(env, more, begin + 513, best);
      best = refined.best;
      candidates = candidates.concat(refined.shortlist.slice(0, 90));
    }
    if (performance.now() < begin + 444 && relevantObjects(env).length > 1) {
      const wings = evaluate(env, wingCandidates(env, rough), begin + 536, best, ['spread']);
      best = wings.best;
      candidates = candidates.concat(wings.shortlist.slice(0, 45));
    }
    if (performance.now() < begin + 435 && (best?.score ?? 0) < 0.63) {
      const focused = seeds.filter(s => s.kind === 'center' || s.kind === 'edge');
      const extra = roughCandidates(env, selectPoses(poses, obs.actor.x), focused, begin + 493, true);
      const dense = evaluate(env, rankedCandidates(extra).slice(0, 90), begin + 555, best);
      best = dense.best;
      candidates = candidates.concat(dense.shortlist.slice(0, 66));
    }
    if (performance.now() < begin + 450) {
      const through = apertureTargets(env, candidates);
      if (through.length) {
        const follow = roughCandidates(env, selectPoses(poses, obs.actor.x), through, begin + 516, true);
        const aperture = evaluate(env, rankedCandidates(follow).slice(0, 103), begin + 570, best, ['single', 'burst', 'spread']);
        best = aperture.best;
        candidates = candidates.concat(aperture.shortlist.slice(0, 70));
      }
    }
    const robust = robustPick(env, candidates, begin + 631);
    if (robust) {
      const chosen = lookahead(env, robust.alternatives, poses, begin + 669);
      best = { ...chosen, score: chosen.robust };
    }
    const fly = findFlight(env, best, absolute);
    if (fly) {
      this.previous = null;
      return fly.action;
    }
    if (unplayable(env, best, this.initialBug)) {
      this.previous = null;
      return { unplayable: true };
    }
    if (best) {
      this.previous = best;
      this.previousTerrain = terrain;
      this.previousObjects = objects;
      if (report) {
        const info = best.impacts.map(i => [Math.round(i.x), Math.round(i.y), i.object?.id ?? i.type]);
        const changes = best.objects.map(o => [o.id, Math.round(o.x), Math.round(o.y), o.hp, o.dead || o.inRecycle || false]);
        process.stderr.write('计划 ' + JSON.stringify({
          n: obs.strokes + 1, ms: Math.round(performance.now() - begin), score: Number(best.score.toFixed(3)),
          from: best.q.pose.actor, impacts: info, objects: changes, penalties: best.penalties,
          bugModels: best.outcomes?.slice(1).map(outcome => {
            const bug = outcome.objects.find(o => o.type === 'bug');
            return bug && [Math.round(bug.x), Math.round(bug.y), outcome.penalties];
          }),
        }) + '\n');
      }
      return best.action;
    }

    const near = relevantObjects(env).reduce((a, b) => Math.abs(a.x - obs.actor.x) < Math.abs(b.x - obs.actor.x) ? a : b);
    const dir = near.x >= obs.actor.x ? 1 : -1;
    const power = clamp(Math.sqrt(Math.abs(near.x - obs.actor.x) / 0.12), 5, 100);
    this.previous = null;
    return { attack: { kind: 'dig', angle: 45, power, dir } };
  }
}
