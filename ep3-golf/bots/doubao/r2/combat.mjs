// 爆炸伤害、弹种选择与目标策略
import { solidAt } from './physics.mjs';

export const ATTACK = {
  single: { damage: 120, crater: 32, dmgR: 30, count: 1 },
  burst:  { damage: 65,  crater: 26, dmgR: 30, count: 3 },
  spread: { damage: 55,  crater: 24, dmgR: 30, count: 3 },
  dig:    { damage: 30,  crater: 60, dmgR: 30, count: 1 },
};

// 物体中心（用于距离判定）
export function center(o) {
  if (o.radius) return { x: o.x, y: o.y - o.radius };
  return { x: o.x, y: o.y - (o.height ?? 28) / 2 };
}

// 线性衰减伤害；隔着地形（中心与爆心连线被实心格挡住）不受溅射
export function blastDamage(kind, impact, obj, terrain) {
  const cfg = ATTACK[kind];
  const c = center(obj);
  const d = Math.hypot(c.x - impact.x, c.y - impact.y);
  if (d > cfg.dmgR) return 0;
  if (terrain && lineBlocked(terrain, impact.x, impact.y, c.x, c.y)) return 0;
  return cfg.damage * (1 - d / cfg.dmgR);
}

export function lineBlocked(t, x0, y0, x1, y1) {
  const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 3));
  for (let i = 1; i < n; i++) {
    const x = x0 + (x1 - x0) * i / n, y = y0 + (y1 - y0) * i / n;
    if (solidAt(t, x, y)) return true;
  }
  return false;
}

export function liveObjects(obs) {
  return (obs.objects || []).filter(o => !o.removed && !o.broken && !o.stunned);
}

// 目标点：token 类打中心；bug 把爆点放在“背离回收区”的一侧，爆炸把 bug 推向回收区。
// 实测（自定义实验 + 练习洞）：
//   gap≈4-13 强推 341~475px；gap≈25-27 中等 86~418px（地形相关，方差大）；
//   反侧 gap≈31-44 温柔反推 31~88px。推力难以精确预测，故采用“远距强推、近距温柔推”，
//   并由评分函数的过冲惩罚兜底。
export function targetPoint(o, hole) {
  const c = center(o);
  if (o.type === 'bug' && hole.recycle) {
    const rc = hole.recycle;
    const rcCenter = (rc.x0 + rc.x1) / 2;
    // 爆点始终放在“背离回收区中心”的一侧：
    //   bug 在回收区左边 -> 爆点放左（向右推）；bug 飞过右边 -> 爆点放右（向左救援）
    const side = rcCenter >= o.x ? -1 : 1;
    const nearEdgeRc = side < 0 ? rc.x0 : rc.x1;
    const dist = Math.abs(o.x - nearEdgeRc);
    // 第一轮+练习实测：近爆推力极不可控（gap27 仍可把 bug 推近 500px 飞过回收区）。
    // 因此近距一律用伤害半径边缘的大 gap 轻推；只有距离很远才用较小 gap。
    const inside = o.x >= rc.x0 && o.x <= rc.x1;
    const gap = inside ? 29 : dist > 350 ? 18 : dist > 200 ? 25 : 29;
    return { x: o.x + side * gap, y: o.y - 12 };
  }
  return c;
}

// 弹种选择：token(1血)/bug 用 single；bigToken/pushToken 按剩余血量与遮挡选。
// burst 同点三发总伤 195（集中，适合单体高血）；spread 三发 ±6° 总伤 165（覆盖广，留给换招）。
export function chooseKind(o, terrain, impact) {
  if (o.type === 'token' || o.type === 'chicken') return 'single';
  if (o.type === 'bug') return 'single'; // 单发坑大、推力集中
  // bigToken / pushToken
  const hp = o.hp ?? 1;
  const c = center(o);
  const gapToTarget = Math.hypot(impact.x - c.x, impact.y - c.y);
  // 只有爆心已经落在目标附近（<=1.5 个伤害半径）且中间确有地形遮挡，才用 dig 挖通道；
  // 爆心在远处墙上时不算“遮挡”，避免第一轮洞06 那种全程 dig 却一发都打不到目标。
  const blocked = terrain && gapToTarget <= 45 &&
    lineBlocked(terrain, impact.x, impact.y, c.x, c.y);
  if (blocked) return 'dig';
  if (hp <= 120) return 'single';
  return 'burst'; // 单体高血目标用集中三连发，伤害余量最大
}

// 评估一次攻击对所有存活物体的期望效果（伤害 + 是否误伤鸡 + 是否把 pushToken 推离）
export function evaluateShot(kind, impact, obs, terrain) {
  let killScore = 0;
  let risk = 0;
  const changes = [];
  for (const o of liveObjects(obs)) {
    const dmg = blastDamage(kind, impact, o, terrain);
    if (dmg <= 0) continue;
    if (o.type === 'chicken') {
      // blastDamage 已排除地形遮挡（dmg=0 会在上面 continue），能到这里说明会被炸到。
      const c = center(o);
      const gap = Math.hypot(c.x - impact.x, c.y - impact.y);
      // 第一轮 3 次炸鸡罚杆，显著提高权重：直接命中/近距爆炸压过一切普通击杀收益
      risk += 4000 + (ATTACK[kind].dmgR - gap) * 60;
      continue;
    }
    if (o.type === 'bug') {
      // 朝向回收区的推动收益由调用方结合方向评估，这里给基础分
      changes.push({ id: o.id, dmg, push: true });
      continue;
    }
    const hp = o.hp ?? 1;
    const after = hp - dmg;
    if (after <= 0) killScore += 500;
    else killScore += dmg * 2;
    changes.push({ id: o.id, dmg, hpAfter: after });
  }
  return { killScore, risk, changes };
}
