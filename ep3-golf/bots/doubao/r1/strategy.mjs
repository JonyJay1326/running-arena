// 决策：目标选择、瞄准搜索、纠偏与跨洞记忆
import { readFileSync, writeFileSync, appendFileSync, existsSync, mkdirSync } from 'node:fs';
import {
  decodeTerrain, muzzlePos, simulateShot, findStandY, solidAt,
} from './physics.mjs';
import { ATTACK, liveObjects, targetPoint, chooseKind, evaluateShot, lineBlocked, center } from './combat.mjs';

const MEM_DIR = new URL('./memory/', import.meta.url);
const MEM_FILE = new URL('./memory/hole-log.jsonl', import.meta.url);

function memWrite(obj) {
  try {
    if (!existsSync(MEM_DIR)) mkdirSync(MEM_DIR, { recursive: true });
    appendFileSync(MEM_FILE, JSON.stringify(obj) + '\n');
  } catch { /* 记忆失败不影响出杆 */ }
}

// 读取上一洞积累的全局修正（如系统性落点偏差）
function readBias() {
  try {
    const f = new URL('./memory/bias.json', import.meta.url);
    if (existsSync(f)) return JSON.parse(readFileSync(f, 'utf8'));
  } catch { /* ignore */ }
  return { samples: [] };
}
function writeBias(b) {
  try {
    if (!existsSync(MEM_DIR)) mkdirSync(MEM_DIR, { recursive: true });
    writeFileSync(new URL('./memory/bias.json', import.meta.url), JSON.stringify(b));
  } catch { /* ignore */ }
}

// 每个洞的内部状态
export class HoleBrain {
  constructor(init) {
    this.obs = init;
    this.holeId = init.hole.id;
    this.terrain = decodeTerrain(init.terrain);
    this.history = []; // 每杆 {action, predicted, impact, objectsBefore}
    this.stallCount = 0;
    this.bias = readBias();
    this.lastTargetId = null;
  }

  log(...a) {
    try { process.stderr.write(a.join(' ') + '\n'); } catch { /* ignore */ }
  }

  decide(obs) {
    this.obs = obs;
    this.terrain = decodeTerrain(obs.terrain);
    this.learnFromLast(obs);
    const goal = obs.hole.goal;
    const live = liveObjects(obs);

    // 1) 目标优先级
    const target = this.pickTarget(live, goal);
    if (!target) {
      // 没有可打目标却没结束：声明不可打避免无限空杆
      return { unplayable: true };
    }
    this.lastTargetId = target.id;

    // 2) 瞄准：搜索 (站位, 方向, 角度, 力度)
    let decision = this.searchBestShot(target, live);
    // 3) 防卡死：若连续停滞，强制换参数，避免重复同一动作
    if (decision && this.stallCount >= 1) {
      decision = this.diversify(decision, target, live);
    }
    // 4) 兜底：搜索无解时给朝目标方向的合法攻击，尽量不要 unplayable（后者直接罚杆）
    if (!decision) {
      const dir = target.x >= obs.actor.x ? 1 : -1;
      decision = { kind: 'single', angle: 45, power: 60, dir, moveX: obs.actor.x, impact: null };
      this.log('搜索无解，使用兜底攻击');
    }

    const action = { attack: { kind: decision.kind, angle: Math.round(decision.angle), power: Math.round(decision.power), dir: decision.dir } };
    if (obs.hole.teeActive && decision.moveX != null && Math.abs(decision.moveX - obs.actor.x) > 2) {
      action.moveX = Math.round(decision.moveX);
    }
    this.history.push({ action, predicted: decision.impact, targetId: target.id });
    return action;
  }

  // 停滞时按层级换招：基于“上一杆实际动作”生成候选，强制与最近 3 杆不同
  diversify(decision, target, live) {
    const lastAction = this.history.length ? this.history[this.history.length - 1].action : null;
    const base = lastAction?.attack
      ? { kind: lastAction.attack.kind, angle: lastAction.attack.angle, power: lastAction.attack.power,
          dir: lastAction.attack.dir, moveX: lastAction.moveX ?? decision.moveX, impact: decision.impact }
      : decision;
    const recent = new Set(this.history.slice(-3).map(h => {
      const a = h.action.attack;
      return a ? `${a.kind}|${a.angle}|${a.power}|${a.dir}|${h.action.moveX ?? ''}` : '';
    }));
    const keyOf = c => `${c.kind}|${Math.round(c.angle)}|${Math.round(c.power)}|${c.dir}|${c.moveX ?? ''}`;
    const candidates = [];
    const kinds = ['single', 'burst', 'spread', 'dig'];
    // 力度扰动（随停滞次数增大）
    const scale = Math.min(3, 1 + Math.floor(this.stallCount / 2));
    for (const dP of [-8, 8, -16, 16, -25, 25].map(v => v * scale)) {
      candidates.push({ ...base, power: Math.max(12, Math.min(100, base.power + dP)) });
    }
    for (const dA of [-6, 6, -12, 12, -20, 20].map(v => v * scale)) {
      candidates.push({ ...base, angle: Math.max(5, Math.min(85, base.angle + dA)) });
    }
    // bug 目标保持 single（推力集中可控），不切多发弹；其他目标才换弹种
    if (target.type !== 'bug') {
      for (const k of kinds) if (k !== base.kind) candidates.push({ ...base, kind: k });
    }
    for (const c of candidates) {
      if (!recent.has(keyOf(c))) {
        this.log(`防卡死换招：${base.kind}角${base.angle}力${base.power} -> ${c.kind}角${Math.round(c.angle)}力${Math.round(c.power)}`);
        return c;
      }
    }
    return decision;
  }

  pickTarget(live, goal) {
    const needBug = goal === 'bug' || goal === 'both';
    const needToken = goal === 'tokens' || goal === 'both';
    const tokens = live.filter(o => ['token', 'bigToken', 'pushToken'].includes(o.type));
    const bug = live.find(o => o.type === 'bug');
    // 尖叫鸡不作为目标
    // 先清 token（通常更脆弱、挡路线），再处理 bug
    if (needToken && tokens.length) {
      // 优先普通 token，其次血量低的，再次距离近的
      tokens.sort((a, b) => {
        const ra = a.type === 'token' ? 0 : 1;
        const rb = b.type === 'token' ? 0 : 1;
        if (ra !== rb) return ra - rb;
        const ha = a.hp ?? 1, hb = b.hp ?? 1;
        if (ha !== hb) return ha - hb;
        return Math.abs(a.x - this.obs.actor.x) - Math.abs(b.x - this.obs.actor.x);
      });
      return tokens[0];
    }
    if (needBug && bug && !bug.inRecycle) return bug;
    return null;
  }

  searchBestShot(target, live) {
    const obs = this.obs;
    const t = this.terrain;
    const wind = obs.hole.wind || 0;
    const ga = obs.actor.groundAngle || 0;
    const tee = obs.hole.tee;
    const canMove = obs.hole.teeActive && tee;

    // 站位：当前位 + 发球台内稀疏候选（保证站稳），控制总数
    let stands = [obs.actor.x];
    if (canMove) {
      for (let x = tee.x0 + 10; x <= tee.x1 - 10; x += 40) {
        if (findStandY(t, x, tee.minFeet) != null) stands.push(x);
      }
    }
    stands = [...new Set(stands.map(x => Math.round(x)))].slice(0, 8);

    // 几何预估主方向：目标在右用 dir=1，在左用 -1；先搜主方向
    const tp = targetPoint(target, obs.hole);
    const mainDir = tp.x >= obs.actor.x ? 1 : -1;
    const dirs = [mainDir, -mainDir];

    let best = null;
    const consider = (moveX, dir, angle, power) => {
      const actor = { ...obs.actor, x: moveX };
      const muzzle = muzzlePos(actor);
      const imp = simulateShot({
        t, muzzle, angleDeg: angle, power, dir, groundAngle: ga, wind,
        objects: live, groundY,
      });
      if (imp.hit === 'out' || imp.hit === 'timeout') return;
      const cand = this.scoreCandidate(imp, target, live, moveX, dir, angle, power);
      if (cand && (!best || cand.total > best.total)) {
        best = { ...cand, angle, power, dir, moveX, impact: imp, kind: cand.kind };
      }
    };

    // 风高度参考：角色脚底高度（局部地面）
    const groundY = obs.actor.y + 12;
    // 阶段 1：粗网格（angle 步长 5、power 步长 8）
    for (const moveX of stands) {
      for (const dir of dirs) {
        for (let angle = 10; angle <= 85; angle += 5) {
          for (let power = 20; power <= 100; power += 8) {
            consider(moveX, dir, angle, power);
          }
        }
      }
    }
    // 阶段 2：在最优点附近细化（只细化前 3 个候选站位/方向组合）
    if (best) {
      const refineSeeds = [{ moveX: best.moveX, dir: best.dir }];
      for (const moveX of stands) for (const dir of dirs) {
        if (!refineSeeds.some(s => s.moveX === moveX && s.dir === dir)) {
          refineSeeds.push({ moveX, dir });
        }
        if (refineSeeds.length >= 3) break;
      }
      for (const seed of refineSeeds) {
        for (let angle = Math.max(5, best.angle - 6); angle <= Math.min(85, best.angle + 6); angle += 2) {
          for (let power = Math.max(10, best.power - 9); power <= Math.min(100, best.power + 9); power += 3) {
            consider(seed.moveX, seed.dir, angle, power);
          }
        }
      }
    }
    return best;
  }

  scoreCandidate(imp, target, live, moveX, dir, angle, power) {
    const t = this.terrain;
    const tp = targetPoint(target, this.obs.hole);
    const dist = Math.hypot(imp.x - tp.x, imp.y - tp.y);
    // 弹种
    const kind = chooseKind(target, t, imp);
    const ev = evaluateShot(kind, imp, this.obs, t);

    let total = -dist * 0.3; // 距离作为次要权重，避免压过方向性
    total += ev.killScore;
    total -= ev.risk; // 尖叫鸡风险作为重罚（但不硬排除，保证总有解）
    // 击中目标物体本身额外加分
    if (imp.hit === 'object' && imp.objectId === target.id) total += 400;
    // bug 推动方向评估：物体被推离爆心，即推动方向 = sign(target.x - imp.x)
    if (target.type === 'bug' && this.obs.hole.recycle) {
      const rc = this.obs.hole.recycle;
      const rcCenter = (rc.x0 + rc.x1) / 2;
      const pushDir = Math.sign(target.x - imp.x) || 1;
      const desired = Math.sign(rcCenter - target.x) || 1;
      const gap = Math.abs(target.x - imp.x);
      const overshot = (desired > 0 && target.x > rc.x1) || (desired < 0 && target.x < rc.x0);
      const inside = target.x >= rc.x0 && target.x <= rc.x1;

      // 主导项：推动方向是否朝向回收区（含过冲后的救援方向）
      if (pushDir === desired) {
        total += 1000;
        const nearEdgeRc = desired > 0 ? rc.x0 : rc.x1;
        const distToEdge = Math.abs(target.x - nearEdgeRc);
        // 实测推力档：gap<15 约 350-475；gap 15-23 约 200-400；gap>=24 约 90-420（方差大）
        const estPush = gap < 15 ? 400 : gap < 24 ? 280 : 150;
        if (!inside && estPush > distToEdge + 80) total -= 500;       // 过冲风险
        if (!inside && Math.abs(estPush - distToEdge) < 100) total += 200;
        if (distToEdge > 300 && gap < 16) total += 150;
        if ((distToEdge <= 150 || inside) && gap >= 26) total += 250; // 近距温柔推
      } else if (overshot) {
        // 飞过回收区却还往更外推：错误，重罚
        total -= 1200;
      } else {
        total -= 1000; // 尚未到达就往反方向推
      }
      // 防止炸出地图
      const nearEdge = target.x < 120 || target.x > 1480;
      if (nearEdge && ((pushDir < 0 && target.x < 120) || (pushDir > 0 && target.x > 1480))) {
        total -= 2000;
      }
    }
    // 避免炸到 pushToken 使其掉坑：若目标不是它，误伤降权；脚下无支撑时重罚
    for (const c of ev.changes) {
      const o = live.find(z => z.id === c.id);
      if (!o || c.id === target.id) continue;
      if (o.type === 'pushToken') {
        total -= 150;
        // 检查 pushToken 脚下及被推方向是否有悬空（4/8/12 格下方无实心）
        const pushDir = Math.sign(o.x - imp.x) || 1;
        const probeX = o.x + pushDir * 20;
        let supported = false;
        for (let dy = 2; dy <= 40; dy += 4) {
          if (solidAt(this.terrain, probeX, o.y + dy) || solidAt(this.terrain, o.x, o.y + dy)) {
            supported = true; break;
          }
        }
        if (!supported) total -= 600;
      }
      if (o.type === 'bigToken') total -= 50;
    }
    // 低 power 小幅优先（可控、减少地形过度破坏）
    total -= power * 0.15;
    // 移动站位的成本（很小）
    total -= Math.abs(moveX - this.obs.actor.x) * 0.02;
    return { total, kind };
  }

  // 从上一杆结果学习：记录预测 vs 实际落点，连续无进展则触发策略切换
  learnFromLast(obs) {
    const lr = obs.lastResult;
    if (!lr || !this.history.length) { this.prevSnapshot = this.snapshot(obs); return; }
    const last = this.history[this.history.length - 1];
    const imps = lr.impacts || [];
    const actual = imps[0];
    const pred = last.predicted;
    if (pred && actual) {
      const dx = actual.x - pred.x, dy = actual.y - pred.y;
      // 只在偏差显著时记录到跨洞偏差本
      if (Math.abs(dx) > 12 || Math.abs(dy) > 12) {
        this.bias.samples.push({
          hole: this.holeId, wind: obs.hole.wind, angle: last.action.attack?.angle,
          power: last.action.attack?.power, dx: +dx.toFixed(1), dy: +dy.toFixed(1),
        });
        if (this.bias.samples.length > 40) this.bias.samples.shift();
        writeBias(this.bias);
      }
    }
    // 进展判断：对比上一杆物体快照（位移/掉血/破碎/回收）
    const progressed = this.detectProgress(obs);
    const penalized = (lr.penalties || []).length > 0;
    if (!progressed || penalized) this.stallCount++;
    else this.stallCount = 0;
    this.prevSnapshot = this.snapshot(obs);
    if (actual) {
      this.log(`杆后：预测(${pred?.x.toFixed(0)},${pred?.y.toFixed(0)}) 实际(${actual.x.toFixed(0)},${actual.y.toFixed(0)}) 停滞=${this.stallCount}`);
    }
  }

  snapshot(obs) {
    return new Map((obs.objects || []).map(o => [o.id, {
      x: o.x, y: o.y, hp: o.hp, broken: o.broken, removed: o.removed,
      stunned: o.stunned, inRecycle: o.inRecycle,
    }]));
  }

  detectProgress(obs) {
    if (!this.prevSnapshot) return true;
    for (const o of obs.objects || []) {
      const p = this.prevSnapshot.get(o.id);
      if (!p) continue;
      if (o.broken && !p.broken) return true;
      if (o.removed && !p.removed) return true;
      if (o.inRecycle && !p.inRecycle) return true;
      if (o.hp != null && p.hp != null && o.hp < p.hp - 0.5) return true;
      if (Math.abs(o.x - p.x) > 6 || Math.abs(o.y - p.y) > 6) return true;
    }
    return false;
  }

  finish(obs, completed) {
    memWrite({
      hole: this.holeId, strokes: obs.strokes, penalties: obs.penalties,
      completed, stalls: this.stallCount, at: new Date().toISOString(),
    });
  }
}
