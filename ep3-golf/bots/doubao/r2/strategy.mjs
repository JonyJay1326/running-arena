// 决策：目标选择、瞄准搜索、纠偏与跨洞记忆
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import {
  decodeTerrain, muzzlePos, simulateShot, findStandY, solidAt,
} from './physics.mjs';
import { ATTACK, liveObjects, targetPoint, chooseKind, evaluateShot, lineBlocked, center } from './combat.mjs';

const MEM_DIR = new URL('./memory/', import.meta.url);

// 读取上一洞积累的全局修正（仅通用物理偏差，不含洞标识）
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

    // 2) 瞄准：搜索 (站位, 方向, 角度, 力度)，搜索内部会结合停滞历史换探索区域
    let decision = this.searchBestShot(target, live);
    // 3) 防卡死：若连续停滞，在搜索结果基础上再强制换角度/站位/弹种，避免重复或单调减力
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

  // 停滞时的针对性再搜索：按停滞轮次切换“探索策略”，用真实弹道模拟挑可达落点，
  // 避免第一轮那种只会单调减 power、落点越打越近的烂尾。
  diversify(decision, target, live) {
    const obs = this.obs;
    const t = this.terrain;
    const wind = obs.hole.wind || 0;
    const ga = obs.actor.groundAngle || 0;
    const groundY = obs.actor.y + 12;
    const recent = new Set(this.history.slice(-3).map(h => {
      const a = h.action.attack;
      return a ? `${a.kind}|${a.angle}|${a.power}|${a.dir}|${h.action.moveX ?? ''}` : '';
    }));
    const keyOf = c => `${c.kind}|${Math.round(c.angle)}|${c.power}|${c.dir}|${c.moveX ?? ''}`;

    // 候选站位：发球台内全部可站稳点（停滞时愿意多移动），外加当前位
    const tee = obs.hole.tee;
    let stands = [obs.actor.x];
    if (obs.hole.teeActive && tee) {
      for (let x = tee.x0; x <= tee.x1; x += 12) {
        if (findStandY(t, x, tee.minFeet) != null) stands.push(x);
      }
    }
    stands = [...new Set(stands.map(x => Math.round(x)))];

    // 探索模式随停滞次数轮换：1 高角吊射越障；2 低角平射；3 换站位；4 换弹种；5 反向
    const mode = (this.stallCount - 1) % 5;
    const angleRanges = [
      [55, 85], // 高角：越过中间地形/打到高处同层目标
      [8, 35],  // 低角：平直快速通道
      [20, 75], // 换站位时全角度
      [30, 70],
      [20, 75],
    ];
    const [aLo, aHi] = angleRanges[mode];
    const tp = targetPoint(target, obs.hole);
    const mainDir = tp.x >= obs.actor.x ? 1 : -1;
    const dirs = mode === 4 ? [-mainDir, mainDir] : [mainDir, -mainDir];
    const kinds = target.type === 'bug' ? ['single']
      : mode === 3 ? ['burst', 'single', 'spread'] : [decision.kind, 'single'];

    // 最近 3 杆的实际落点：停滞再搜索时强制远离这些“已证明无效”的落点
    const recentImpacts = this.history
      .map(h => h.predicted).filter(p => p && Number.isFinite(p.x)).slice(-3);
    let best = null;
    const consider = (moveX, dir, angle, power, kind) => {
      const actor = { ...obs.actor, x: moveX };
      const muzzle = muzzlePos(actor);
      const imp = simulateShot({ t, muzzle, angleDeg: angle, power, dir, groundAngle: ga, wind, objects: live, groundY });
      if (imp.hit === 'out' || imp.hit === 'timeout') return;
      const cand = this.scoreCandidate(imp, target, live, moveX, dir, angle, power);
      if (!cand) return;
      cand.kind = kind;
      // 落点到目标的物理距离是停滞时的首要指标
      cand.total -= Math.hypot(imp.x - tp.x, imp.y - tp.y) * 0.5;
      // 与最近无效落点过近则重罚，迫使弹道换到新的落点/通道
      for (const ri of recentImpacts) {
        if (Math.hypot(imp.x - ri.x, imp.y - ri.y) < 45) { cand.total -= 700; break; }
      }
      if (!best || cand.total > best.total) {
        best = { ...cand, angle, power, dir, moveX, impact: imp, kind };
      }
    };
    const standList = mode === 2 ? stands : stands.slice(0, 4);
    for (const moveX of standList) {
      for (const dir of dirs) {
        for (const kind of kinds) {
          for (let angle = aLo; angle <= aHi; angle += 3) {
            for (let power = 20; power <= 100; power += 5) {
              consider(moveX, dir, angle, power, kind);
            }
          }
        }
      }
    }
    if (best && !recent.has(keyOf(best))) {
      this.log(`停滞换招(mode${mode})：-> ${best.kind}角${best.angle}力${best.power}向${best.dir}站${best.moveX}`);
      return best;
    }
    // 兜底：在原决策上强制改角度，至少不重复
    if (best) return best;
    const last = this.history[this.history.length - 1]?.action?.attack;
    const shift = (this.stallCount % 2 === 0 ? 12 : -12);
    return { ...decision, angle: Math.max(5, Math.min(85, (last?.angle ?? decision.angle) + shift)) };
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

    // 水平距离与垂直距离分开评：高处同层目标（洞03/09 类）垂直匹配很关键
    const dx = Math.abs(imp.x - tp.x);
    const dy = Math.abs(imp.y - tp.y);
    // 水平距离是主导项（坑洞场景不能因垂直接近就停在半路），垂直匹配作为同水平带内的细化
    let total = -dx * 0.9 - dy * 0.5;
    // 弹道可达性：只有水平也接近时，高度匹配才给奖励，避免“近而不达”的坑壁落点骗分
    if (dy <= 40 && dx <= 120) total += 120;
    if (dy <= 20 && dx <= 60) total += 200;
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
        // 实测推力方差极大（gap27 仍可能推近 500px），近距时只信任大 gap 的边缘轻推
        const estPush = gap < 15 ? 450 : gap < 24 ? 320 : gap < 28 ? 220 : 120;
        if (!inside && estPush > distToEdge + 40) total -= 900;       // 过冲风险，重罚
        if (!inside && Math.abs(estPush - distToEdge) < 100) total += 200;
        if (distToEdge > 300 && gap < 18) total += 150;
        if ((distToEdge <= 180 || inside) && gap >= 28) total += 350; // 近距只用边缘温柔推
        if (distToEdge <= 180 && gap < 24) total -= 600;              // 近距严禁近爆
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
      // 只在偏差显著时记录到跨洞偏差本；只存通用物理量，不存任何洞标识/坐标指纹
      if (Math.abs(dx) > 12 || Math.abs(dy) > 12) {
        this.bias.samples.push({
          wind: obs.hole.wind, angle: last.action.attack?.angle,
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
    // 第二轮合规：不写任何含洞标识/洞序的跨洞记录；仅保留进程内统计，不落盘洞信息
    try { process.stderr.write(`洞结束 完成=${completed} 杆=${obs.strokes} 罚=${obs.penalties}\n`); } catch { /* ignore */ }
  }
}
