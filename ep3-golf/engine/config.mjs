function freeze(value) {
  for (const child of Object.values(value)) if (child && typeof child === 'object') freeze(child);
  return Object.freeze(value);
}
export const CONFIG = freeze({
  RULES_VERSION: 'golf-v1', PROTOCOL_VERSION: 1, REPLAY_VERSION: 1, engineeringOnly: true,
  W: 1600, H: 900, CELL: 4, GW: 400, GH: 225,
  G: 320, TICK: 1 / 60, WIND_K: 12, V_K: 6.2, R: 12, STEP_MAX: 16,
  // 高尔夫人物缩小到 0.6 倍（2026-11-11）：判定和画面一起缩；footOffset 是内部参考点到脚底的距离，不变
  BODY: { halfWidth: 13, height: 60, footOffset: 12, muzzleHeight: 35, visualHeight: 70, maxTilt: 45, slopeFitSpan: 8, supportProbe: 16 },
  PRACTICE_PROBES: 16,
  INIT_DEADLINE_MS: 5000, TURN_DEADLINE_MS: 1000,
  // 风层（S 形，2026-11-11 校准）：边界 y=400 以下是显示风力（×1.0），以上为急流层 ×1.35
  // 2026-11-12 过渡宽 40 → 120（y 460～340 之间平滑变大，图上能看出 S 形）；宽度对校准 Bot 误差几乎无影响，见 output/width-sweep-20261112/
  SHEAR: { type: 'layered', lo: 1, hi: 1.35, boundary: 400, width: 120 },
  // 2026-11-13 新批次：Bug 击退速度下调；旧成绩使用归档的 420 版本。
  KNOCKBACK: { radius: 60, speed: 380, lift: .35, friction: 600 },
  // 滑板独立于 Bug：相对质量削弱每次冲量，上抛和水平方向分别限制速度叠加。
  PUSH_TOKEN_KNOCKBACK: { radius: 60, impulseSpeed: 420, mass: 2.5, lift: .12, maxHorizontalSpeed: 220, maxUpwardSpeed: 90, friction: 600 },
  OBJECTS: { token: { width: 28, height: 28, hp: 1 }, bigToken: { width: 44, height: 56, hp: 240 }, chicken: { width: 26, height: 30, hp: 1 }, bug: { radius: 14 },
    // 机关：开关（一碰即碎，不算目标）打碎后，对应的门立刻消失；门打不坏、不受重力，只挡炮弹
    // 滑板 token：有独立重量和击退上限，落地滑行；掉出地图算打碎（"埋了"）
    pushToken: { width: 30, height: 30, hp: 150 },
    switch: { width: 24, height: 24, hp: 1 }, gate: { width: 16, height: 120, hp: null } },
  ATTACKS: {
    single: { damage: 120, radius: 32, damageRadius: 30, offsets: [0], ticks: [0] },
    burst: { damage: 65, radius: 26, damageRadius: 30, offsets: [0], ticks: [0, 48, 96] },
    spread: { damage: 55, radius: 24, damageRadius: 30, offsets: [-6, 0, 6], ticks: [0] },
    dig: { damage: 30, radius: 60, damageRadius: 30, offsets: [0], ticks: [0] },
  },
});
export function windFactor(y, parameters = CONFIG.SHEAR) {
  if (parameters.type === 'layered') {
    const u = Math.max(0, Math.min(1, (parameters.boundary + parameters.width / 2 - y) / parameters.width));
    return parameters.lo + (parameters.hi - parameters.lo) * u * u * (3 - 2 * u);
  }
  return Math.max(parameters.min, Math.min(parameters.max, parameters.base + parameters.slope * (parameters.referenceY - y)));
}
export function publicConfig() {
  const { G, TICK, WIND_K, V_K, R, SHEAR, KNOCKBACK, PUSH_TOKEN_KNOCKBACK, ...visible } = CONFIG;
  return structuredClone(visible);
}
