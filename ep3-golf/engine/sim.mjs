import { CONFIG as C, publicConfig } from './config.mjs';
import { standingPose, carveGrid } from './geometry.mjs';
import { center, surfaceBelow, intersectsTerrain, ejectFromTerrain, visibleObjectDistance } from './objects.mjs';
import { projectile, stepProjectile, sampleProjectile } from './physics.mjs';
import { loadMap, validateHole, encodeRLE, decodeRLE, sha, applyTerrainEdits } from './maps.mjs';
export { C, publicConfig };
const clone = structuredClone;
const EPS = 1e-6;
const isRecord = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const keys = (v, allowed) => Object.keys(v).every(k => allowed.includes(k));

export function poseAt(grid, x, minFeet = 0) {
  const tops = new Set();
  for (let gx = Math.max(0, Math.floor((x - C.BODY.halfWidth) / C.CELL)); gx <= Math.min(C.GW - 1, Math.floor((x + C.BODY.halfWidth) / C.CELL)); gx++) {
    for (let gy = Math.max(0, Math.floor(minFeet / C.CELL)); gy < C.GH; gy++) if (grid[gy * C.GW + gx] && (gy === 0 || !grid[(gy - 1) * C.GW + gx])) tops.add(gy * C.CELL);
  }
  for (const y of [...tops].sort((a, b) => a - b)) {
    const pose = standingPose(grid, { x, y: y - C.BODY.footOffset, groundAngle: 0 }, [], { maxRise: 32, maxDrop: 32 });
    if (pose && pose.y + C.BODY.footOffset >= minFeet - EPS) return pose;
  }
  return null;
}
function publicObject(o) {
  return { id: o.id, type: o.type, x: o.x, y: o.y, ...(o.type === 'bug' ? { radius: o.radius } : { width: o.width, height: o.height, hp: o.hp }),
    ...(['bigToken', 'pushToken'].includes(o.type) ? { maxHp: o.maxHp } : {}), ...(o.type === 'switch' ? { opens: o.opens } : {}),
    stage: o.stage, removed: o.removed, broken: o.broken, stunned: o.stunned, inRecycle: o.inRecycle };
}
export class GolfSim {
  constructor(hole, map = loadMap(hole.map)) {
    hole = validateHole(hole); this.hole = hole;
    if (!(map.grid instanceof Uint8Array) || map.grid.length !== C.GW * C.GH || map.grid.some(bit => bit !== 0 && bit !== 1)) throw new Error('无效地形网格');
    this.grid = applyTerrainEdits(map.grid.slice(), hole.terrain); this.initialRLE = encodeRLE(this.grid); this.sources = clone(map.sources ?? {});
    this.objects = hole.objects.map((o, id) => ({ ...C.OBJECTS[o.type], ...o, maxHp: o.hp ?? C.OBJECTS[o.type].hp, id, vx: 0, vy: 0, mode: 'rest', stage: 0,
      removed: false, broken: false, stunned: false, inRecycle: false, initial: { x: o.x, y: o.y } }));
    if (this.objects.some(o => intersectsTerrain(this.grid, o))) throw new Error('初始物体与地形重叠');
    // 发球台可指定高度下限 minFeet（比如在屋子里面而不是屋顶上）
    this.actor = poseAt(this.grid, (hole.tee.x0 + hole.tee.x1) / 2, hole.tee.minFeet ?? 0);
    if (!this.actor) throw new Error('发球台没有合法站位');
    this.strokes = 0; this.rawStrokes = 0; this.penalties = 0; this.shotsTaken = 0; this.fly = hole.fly; this.teeActive = true;
    this.time = 0; this.events = []; this.actions = []; this.lastResult = null; this.nextProjectile = 1; this.finished = false; this.completed = false;
    this.checkCompletion();
    this.events = [];
  }
  emit(type, detail = {}) { this.events.push({ t: this.time, type, ...clone(detail) }); }
  addPenalty(reason, objectId = null) {
    this.rawStrokes++; this.penalties++; this.strokes = Math.min(this.hole.cap, this.rawStrokes);
    this.emit('penalty', { reason, objectId, strokes: this.strokes, penalties: this.penalties });
    this.currentResult.penalties.push({ reason, objectId });
  }
  checkCompletion() {
    const tokens = this.objects.filter(o => ['token', 'bigToken', 'pushToken'].includes(o.type)).every(o => o.broken);
    for (const o of this.objects) if (o.type === 'bug' && !o.inRecycle && !o.removed && o.mode === 'rest' && this.hole.recycle) {
      const c = center(o), r = this.hole.recycle;
      if (c.x >= r.x0 && c.x <= r.x1 && c.y >= r.y0 && c.y <= r.y1) { o.inRecycle = true; this.emit('recycle', { objectId: o.id, x: o.x, y: o.y }); }
    }
    const bug = this.objects.some(o => o.type === 'bug' && o.inRecycle);
    this.completed = this.hole.goal === 'tokens' ? tokens : this.hole.goal === 'bug' ? bug : tokens && bug;
    this.finished = this.completed || this.strokes >= this.hole.cap;
  }
  observe() {
    return { rulesVersion: C.RULES_VERSION, engineeringOnly: true,
      hole: { id: this.hole.id, map: this.hole.map, par: this.hole.par, cap: this.hole.cap, wind: this.hole.wind, goal: this.hole.goal,
        recycle: clone(this.hole.recycle), tee: clone(this.hole.tee), teeActive: this.teeActive },
      strokes: this.strokes, penalties: this.penalties, shotsTaken: this.shotsTaken, fly: this.fly,
      terrain: { width: C.GW, height: C.GH, cell: C.CELL, rle: encodeRLE(this.grid) },
      actor: clone(this.actor), objects: this.objects.map(publicObject), lastResult: clone(this.lastResult),
      finished: this.finished, completed: this.completed };
  }
  validateAction(action) {
    if (this.finished) throw new Error('本洞已结束');
    if (!isRecord(action) || !keys(action, ['moveX', 'attack', 'fly', 'unplayable'])) throw new Error('动作字段无效');
    const endings = ['attack', 'fly', 'unplayable'].filter(k => action[k] !== undefined);
    if (endings.length !== 1) throw new Error('每次只能选择开炮、飞机或不可打');
    const kind = endings[0]; let actor = this.actor;
    if (action.moveX !== undefined) {
      if (!this.teeActive || !Number.isFinite(action.moveX) || action.moveX < this.hole.tee.x0 || action.moveX > this.hole.tee.x1) throw new Error('站位不在有效发球台');
      actor = poseAt(this.grid, action.moveX, this.hole.tee.minFeet ?? 0);
      if (!actor) throw new Error('站位没有合法支撑');
    }
    if (kind === 'unplayable') {
      if (action.unplayable !== true || action.moveX !== undefined) throw new Error('不可打动作无效');
    } else {
      const aim = action[kind];
      if (!actor || !isRecord(aim) || !keys(aim, kind === 'attack' ? ['kind', 'angle', 'power', 'dir'] : ['angle', 'power', 'dir']) ||
        ![aim.angle, aim.power].every(Number.isFinite) || aim.angle < 0 || aim.angle > (kind === 'fly' ? 90 : 85) || aim.power < 0 || aim.power > 100 || ![-1, 1].includes(aim.dir)) throw new Error('瞄准或角色站位无效');
      if (kind === 'attack' && !Object.hasOwn(C.ATTACKS, aim.kind)) throw new Error('非法弹种');
      if (kind === 'fly' && this.fly <= 0) throw new Error('纸飞机配额已用尽');
    }
    return { kind, actor: clone(actor), action: clone(action) };
  }
  execute(action) {
    const plan = this.validateAction(action), before = this.objects.map(publicObject), start = this.time;
    this.actions.push(clone(action)); this.currentResult = { action: plan.kind, command: clone(action), from: clone(plan.actor), impacts: [], objectChanges: [], penalties: [] };
    this.bugBefore = new Map(this.objects.filter(o => o.type === 'bug').map(o => [o.id, { x: o.x, y: o.y }]));
    this.outThisAction = new Set(); this.actor = plan.actor;
    this.emit('action', { index: this.actions.length - 1, action, actor: this.actor });
    if (plan.kind === 'unplayable') {
      // 任何洞都可声明：罚 1 杆；有未入洞的 Bug 则放回初始位置（地形不恢复）
      const bug = this.objects.find(o => o.type === 'bug' && !o.inRecycle);
      this.addPenalty('unplayable', bug?.id ?? null); if (bug) this.resetBug(bug, bug.initial, 'unplayable');
    } else {
      this.rawStrokes++; this.strokes = Math.min(this.rawStrokes, this.hole.cap);
      if (plan.kind === 'attack') this.shotsTaken++;
      else this.fly--;
      this.emit('score', { strokes: this.strokes, penalties: this.penalties, shotsTaken: this.shotsTaken, fly: this.fly });
      this.advance(plan.kind, action[plan.kind]);
      if (plan.kind === 'attack' && this.actor) {
        const pose = poseAt(this.grid, this.actor.x, this.actor.y + C.BODY.footOffset - EPS);
        this.actor = pose;
        this.emit('actor-settle', { actor: this.actor });
      }
    }
    this.checkCompletion();
    this.currentResult.objectChanges = this.objects.map(publicObject).flatMap((o, i) => JSON.stringify(o) === JSON.stringify(before[i]) ? [] : [{ id: o.id, before: before[i], after: o, damage: Math.max(0, (before[i].hp ?? 0) - (o.hp ?? 0)) }]);
    this.lastResult = clone(this.currentResult);
    this.emit('action-end', { index: this.actions.length - 1, start, strokes: this.strokes, penalties: this.penalties, completed: this.completed, finished: this.finished, result: this.lastResult });
    if (this.finished) this.emit('finish', { reason: this.completed ? 'completed' : 'cap', strokes: this.strokes, relativeToPar: this.strokes - this.hole.par });
    return this.observe();
  }
  resetBug(o, position, reason) {
    Object.assign(o, position, { vx: 0, vy: 0, mode: 'rest', inRecycle: false });
    this.outThisAction.add(o.id);
    this.emit('object-reset', { objectId: o.id, x: o.x, y: o.y, reason });
  }
  objectOut(o) {
    if (o.type === 'bug') {
      this.addPenalty('bug-out', o.id); this.resetBug(o, this.bugBefore.get(o.id), 'bug-out');
    } else {
      o.removed = true; o.mode = 'rest'; o.vx = 0; o.vy = 0;
      if (o.type === 'chicken') { o.stunned = true; this.addPenalty('chicken-out', o.id); }
      else { o.broken = true; o.hp = 0; }
      this.emit(o.type === 'chicken' ? 'stun' : 'break', { objectId: o.id, reason: 'out', x: o.x, y: o.y });
      if (o.type === 'switch') this.openGate(o);
    }
  }
  openGate(sw) {
    const gate = this.objects[sw.opens];
    if (!gate || gate.removed) return;
    gate.removed = true; this.emit('gate-open', { objectId: gate.id, switchId: sw.id, x: gate.x, y: gate.y });
  }
  knock(o, distance, hit) {  // 先结算伤害，被打碎的滑板不再施加冲量。
    if (distance < C.PUSH_TOKEN_KNOCKBACK.radius) o.pendingKnock = { distance, hit };
  }
  applyKnock(o) {
    const { distance, hit } = o.pendingKnock; delete o.pendingKnock;
    if (o.removed) return;
    const k = C.PUSH_TOKEN_KNOCKBACK;
    const c = center(o), dx = c.x - hit.x, dy = c.y - hit.y, length = Math.hypot(dx, dy), magnitude = k.impulseSpeed / k.mass * (1 - distance / k.radius);
    const vx = o.vx + magnitude * (length > EPS ? dx / length : 0);
    const vy = o.vy + magnitude * (length > EPS ? dy / length : -1) - k.lift * magnitude;
    o.vx = Math.max(-k.maxHorizontalSpeed, Math.min(k.maxHorizontalSpeed, vx));
    // 只限制上抛，不截断重力导致的自然下落速度。
    o.vy = Math.max(-k.maxUpwardSpeed, vy);
    o.mode = 'flight'; this.emit('knockback', { objectId: o.id, projectileId: hit.shot.id, vx: o.vx, vy: o.vy, distance });
  }
  explode(hit) {
    const profile = C.ATTACKS[hit.shot.kind];
    this.emit('explosion', { projectileId: hit.shot.id, kind: hit.shot.kind, x: hit.x, y: hit.y, radius: profile.radius, damageRadius: profile.damageRadius });
    for (const o of this.objects) {
      if (o.removed || o.inRecycle || this.outThisAction.has(o.id) || o.type === 'gate') continue;
      const direct = hit.target?.id === o.id;
      if (o.type === 'bug') {
        const distance = direct ? 0 : visibleObjectDistance(this.grid, o, hit.x, hit.y, C.KNOCKBACK.radius);
        if (distance < C.KNOCKBACK.radius) {
          const c = center(o), dx = c.x - hit.x, dy = c.y - hit.y, length = Math.hypot(dx, dy), magnitude = C.KNOCKBACK.speed * (1 - distance / C.KNOCKBACK.radius);
          // 同一杆逐颗追加速度；爆心与中心重合时取向上，避免未定义方向。
          o.vx += magnitude * (length > EPS ? dx / length : 0);
          o.vy += magnitude * (length > EPS ? dy / length : -1) - C.KNOCKBACK.lift * magnitude;
          o.mode = 'flight'; this.emit('knockback', { objectId: o.id, projectileId: hit.shot.id, vx: o.vx, vy: o.vy, distance });
        }
      } else {
        if (o.type === 'pushToken') this.knock(o, direct ? 0 : visibleObjectDistance(this.grid, o, hit.x, hit.y, C.PUSH_TOKEN_KNOCKBACK.radius), hit);
        const distance = direct ? 0 : visibleObjectDistance(this.grid, o, hit.x, hit.y, profile.damageRadius);
        const amount = Math.round(profile.damage * Math.max(0, 1 - distance / profile.damageRadius));
        if (amount <= 0) continue;
        const damage = Math.min(o.hp, amount); o.hp -= damage;
        this.emit('damage', { objectId: o.id, projectileId: hit.shot.id, amount: damage, hp: o.hp, direct });
        if (o.type === 'bigToken') {
          const stage = o.hp * 3 <= o.maxHp ? 2 : o.hp * 3 <= o.maxHp * 2 ? 1 : 0;
          if (stage !== o.stage) { o.stage = stage; this.emit('crack', { objectId: o.id, stage }); }
        }
        if (o.hp <= 0) {
          o.removed = true; o.vx = 0; o.vy = 0; o.mode = 'rest';
          if (o.type === 'chicken') { o.stunned = true; this.addPenalty('chicken-damage', o.id); }
          else o.broken = true;
          this.emit(o.type === 'chicken' ? 'stun' : 'break', { objectId: o.id, reason: 'damage', x: o.x, y: o.y });
          if (o.type === 'switch') this.openGate(o);
        }
      }
    }
    for (const o of this.objects) if (o.pendingKnock) this.applyKnock(o);
    const before = this.grid.slice(); carveGrid(this.grid, hit.x, hit.y, profile.radius);
    const removedCells = [];
    for (let i = 0; i < this.grid.length; i++) if (before[i] && !this.grid[i]) removedCells.push(i);
    this.emit('terrain-carve', { projectileId: hit.shot.id, x: hit.x, y: hit.y, radius: profile.radius, removedCells, gridSha256: sha(this.grid) });
  }
  advanceObjects(dt) {
    for (const o of this.objects) {
      if (o.removed || o.inRecycle || this.outThisAction.has(o.id) || o.type === 'gate') continue;
      const support = surfaceBelow(this.grid, o, .05, .1);
      if (o.mode === 'rest') {
        if (support !== null) continue;
        o.mode = 'flight'; o.vx = 0; o.vy = 0;
        this.emit('fall', { objectId: o.id, x: o.x, y: o.y });
      }
      const before = { x: o.x, y: o.y };
      if (o.mode === 'slide') {
        const here = surfaceBelow(this.grid, o, 4, 8);
        if (here === null) o.mode = 'flight';
        else {
          const left = surfaceBelow(this.grid, { ...o, x: o.x - 2 }, 8, 8), right = surfaceBelow(this.grid, { ...o, x: o.x + 2 }, 8, 8);
          const slope = left !== null && right !== null ? (right - left) / 4 : 0;
          o.vx += C.G * slope / (1 + slope * slope) * dt;
          const friction = (o.type === 'pushToken' ? C.PUSH_TOKEN_KNOCKBACK.friction : C.KNOCKBACK.friction) * dt;
          o.vx = Math.sign(o.vx) * Math.max(0, Math.abs(o.vx) - friction);
          o.x += o.vx * dt;
          const next = surfaceBelow(this.grid, o, Math.max(4, Math.abs(o.vx * dt) + 1), 8);
          if (next !== null) o.y = next;
          else o.mode = 'flight';
          if (intersectsTerrain(this.grid, o)) { Object.assign(o, before); o.vx = 0; }
          if (o.mode === 'slide' && Math.abs(o.vx) < .01) { o.vx = 0; o.mode = 'rest'; this.emit('rest', { objectId: o.id, x: o.x, y: o.y }); }
        }
      }
      if (o.mode === 'flight') {
        o.vy += C.G * dt; o.x += o.vx * dt; o.y += o.vy * dt;
        const floor = o.vy >= 0 ? surfaceBelow(this.grid, { ...o, y: before.y }, 1, Math.max(2, o.y - before.y + 1)) : null;
        if (floor !== null && o.y >= floor) {
          o.y = floor; o.vy = 0; o.mode = (o.type === 'bug' || o.type === 'pushToken') && Math.abs(o.vx) > .01 ? 'slide' : 'rest';
          this.emit('land', { objectId: o.id, x: o.x, y: o.y, mode: o.mode, vx: o.vx });
        } else if (intersectsTerrain(this.grid, o)) {
          Object.assign(o, before); o.vx = 0; o.vy = 0;
          // 移动前就已嵌入（如静止时侧边贴进墙格、脚下随后被炸空）：先推出到最近的空位，否则每个子步都会回退、悬停到结算上限。
          if (intersectsTerrain(this.grid, o)) ejectFromTerrain(this.grid, o);
          o.mode = surfaceBelow(this.grid, o, .1, .1) !== null ? 'rest' : 'flight';
          this.emit('terrain-contact', { objectId: o.id, x: o.x, y: o.y });
        }
      }
      if (o.x < 0 || o.x >= C.W || o.y >= C.H) this.objectOut(o);
    }
  }
  advance(kind, aim) {
    const start = this.time, shots = [], tracks = new Map(this.objects.map(o => [o.id, { objectId: o.id, pts: [[o.x, o.y]], times: [start], modes: [o.mode] }]));
    const profile = kind === 'attack' ? C.ATTACKS[aim.kind] : { ticks: [0], offsets: [0] };
    const schedule = profile.ticks.flatMap(tick => profile.offsets.map(offset => ({ tick, offset })));
    for (let tick = 0; tick < 1200; tick++) {
      for (const item of schedule.filter(s => s.tick === tick)) {
        const p = projectile(this.actor, aim, { kind: kind === 'fly' ? 'plane' : aim.kind, offset: item.offset, id: this.nextProjectile++, plane: kind === 'fly', start: start + tick * C.TICK });
        shots.push(p); this.emit('launch', { projectileId: p.id, kind: p.kind, angle: p.worldAngle, x: p.x, y: p.y });
      }
      const maxSpeed = Math.max(1000, ...shots.filter(p => p.active).map(p => Math.hypot(p.vx, p.vy) + C.G * C.TICK), ...this.objects.filter(o => !o.removed).map(o => Math.hypot(o.vx, o.vy) + C.G * C.TICK));
      const n = Math.ceil(maxSpeed * C.TICK / 1.5), dt = C.TICK / n;
      for (let sub = 0; sub < n; sub++) {
        this.time = start + (tick + (sub + 1) / n) * C.TICK;
        this.advanceObjects(dt);
        for (const p of shots) {
          const hit = stepProjectile(p, { grid: this.grid, objects: this.objects, wind: this.hole.wind }, dt, this.time);
          if (!hit) continue;
          this.currentResult.impacts.push({ projectileId: p.id, kind: p.kind, ...clone(p.land) });
          this.emit('impact', { projectileId: p.id, ...p.land });
          if (kind === 'fly') {
            const pose = !hit.out ? poseAt(this.grid, hit.x, Math.max(0, hit.y - 32)) : null;
            if (pose) { this.actor = pose; this.teeActive = false; }
            this.emit('plane-arrival', { success: Boolean(pose), actor: this.actor });
          } else if (!hit.out) this.explode(hit);
        }
        this.checkCompletion();
      }
      this.time = start + (tick + 1) * C.TICK;
      for (const p of shots) if (p.active) sampleProjectile(p, this.time);
      for (const o of this.objects) {
        const tr = tracks.get(o.id), last = tr.pts.at(-1);
        if (last[0] !== o.x || last[1] !== o.y || tr.modes.at(-1) !== o.mode) { tr.pts.push([o.x, o.y]); tr.times.push(this.time); tr.modes.push(o.mode); }
      }
      const moving = this.objects.some(o => !o.removed && !o.inRecycle && !this.outThisAction.has(o.id) && o.type !== 'gate' && (o.mode !== 'rest' || surfaceBelow(this.grid, o, .05, .1) === null));
      if (!shots.some(p => p.active) && !moving && !schedule.some(s => s.tick > tick)) {
        for (const p of shots) this.emit('trajectory', { projectileId: p.id, kind: p.kind, pts: p.pts, times: p.times, land: p.land });
        for (const track of tracks.values()) if (track.pts.length > 1) this.emit('object-trajectory', track);
        return;
      }
    }
    throw new Error('整杆结算超过20秒，技术失败，不生成有效成绩');
  }
  replay() {
    return { format: 'golf-v1', version: C.REPLAY_VERSION, engineeringOnly: true, config: clone(C), hole: clone(this.hole),
      initialTerrain: { rle: clone(this.initialRLE), gridSha256: sha(decodeRLE(this.initialRLE)), sources: clone(this.sources) },
      actions: clone(this.actions), events: clone(this.events), final: this.observe(), finalGridSha256: sha(this.grid) };
  }
}
export function verifyReplay(replay) {
  if (replay.format !== 'golf-v1' || replay.version !== C.REPLAY_VERSION || JSON.stringify(replay.config) !== JSON.stringify(C)) throw new Error('回放版本或物理配置不符');
  const grid = decodeRLE(replay.initialTerrain.rle);
  if (sha(grid) !== replay.initialTerrain.gridSha256) throw new Error('回放初始地形hash不符');
  const sim = new GolfSim(replay.hole, { grid, sources: replay.initialTerrain.sources });
  for (const action of replay.actions) sim.execute(action);
  const rebuilt = sim.replay();
  if (JSON.stringify(rebuilt.events) !== JSON.stringify(replay.events) || JSON.stringify(rebuilt.final) !== JSON.stringify(replay.final) || rebuilt.finalGridSha256 !== replay.finalGridSha256) throw new Error('回放复算不符');
  return sim;
}
