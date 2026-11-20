function freeze(value) { for (const child of Object.values(value)) if (child && typeof child === 'object') freeze(child); return Object.freeze(value); }
export const CONFIG = freeze({
  RULES_VERSION: 'simultaneous-v1', PROTOCOL_VERSION: 5, REPLAY_VERSION: 3,
  W: 1600, H: 900, CELL: 4, GW: 400, GH: 225,
  G: 320, TICK: 1 / 60, WIND_K: 12, V_K: 6.2, R: 12,
  BODY: { halfWidth: 22, height: 100, footOffset: 12, muzzleHeight: 58, visualHeight: 116, maxTilt: 45, slopeFitSpan: 8, supportProbe: 16 },
  HP0: 1000, STAMINA: 100, MOVE_MAX: 100, MOVE_PER_STAMINA: 4, STEP_MAX: 16, FALL_SAFE: 120,
  ROUNDS_MAX: 20, INIT_DEADLINE_MS: 5000, TURN_DEADLINE_MS: 1000, MAX_VIOLATIONS: 3,
  RAGE_MAX: 100, RAGE_TAKE: .1, RAGE_DEAL: .05,
  ITEMS: { heal: { count: 1, amount: 300, cost: 30 }, fly: { count: 2, cost: 80 } },
  ATTACKS: {
    single: { damage: 120, cost: 25, radius: 32, damageRadius: 30, offsets: [0], ticks: [0] },
    burst: { damage: 65, cost: 80, radius: 26, damageRadius: 30, offsets: [0], ticks: [0, 48, 96] },
    spread: { damage: 55, cost: 55, radius: 24, damageRadius: 30, offsets: [-6, 0, 6], ticks: [0] },
    dig: { damage: 30, cost: 60, radius: 60, damageRadius: 30, offsets: [0], ticks: [0] },
    ultimate: { damage: 240, cost: 60, radius: 48, damageRadius: 45, offsets: [0], ticks: [0] },
  },
});

export function publicConfig() {
  const { G, TICK, WIND_K, V_K, R, ...visible } = CONFIG;
  return structuredClone(visible);
}
