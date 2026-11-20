// bot/memory.mjs — 跨洞与洞内在线校准记忆（只读写 bot/memory/）
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';

const MEM_DIR = new URL('./memory/', import.meta.url);
const CAL_FILE = new URL('./memory/calibration.json', import.meta.url);

export const DEFAULT_CAL = {
  version: 4,
  useShapeDist: true,
  knockRadius: 52,
  bugVmax: 800,
  pushVmax: 500,
  bodyGravity: 1000,
  bugSlideK: 0.65,
  pushSlideK: 0.45,
  bugSamples: [],
  pushSamples: [],
};

export function loadCalibration() {
  try {
    if (!existsSync(CAL_FILE)) return structuredClone(DEFAULT_CAL);
    const raw = JSON.parse(readFileSync(CAL_FILE, 'utf8'));
    if (raw.version !== 4) return structuredClone(DEFAULT_CAL);
    return { ...structuredClone(DEFAULT_CAL), ...raw };
  } catch {
    return structuredClone(DEFAULT_CAL);
  }
}

export function saveCalibration(cal) {
  try {
    if (!existsSync(MEM_DIR)) mkdirSync(MEM_DIR, { recursive: true });
    if (cal.bugSamples.length > 40) cal.bugSamples = cal.bugSamples.slice(-40);
    if (cal.pushSamples.length > 40) cal.pushSamples = cal.pushSamples.slice(-40);
    writeFileSync(CAL_FILE, JSON.stringify(cal, null, 2), 'utf8');
  } catch {
    // Never fail turn on filesystem issue
  }
}

export function updateCalibrationFromResult(cal, lastResult, prevObjects) {
  if (!lastResult || !Array.isArray(lastResult.impacts) || !Array.isArray(lastResult.objectChanges)) {
    return cal;
  }
  if (lastResult.impacts.length !== 1) return cal;
  const imp = lastResult.impacts[0];
  if (imp.hit === 'out') return cal;

  const byId = new Map((prevObjects || []).map(o => [o.id, o]));
  let updated = false;

  for (const chg of lastResult.objectChanges) {
    const prev = byId.get(chg.id);
    if (!prev || !chg.before || !chg.after) continue;
    if (chg.after.removed || chg.after.broken) continue;

    if (prev.type === 'bug' || prev.type === 'pushToken') {
      const r = prev.type === 'bug' ? (prev.radius ?? 14) : ((prev.height ?? 30) * 0.5);
      const cx = chg.before.x;
      const cy = chg.before.y - r;
      const dx = cx - imp.x;
      const dy = cy - imp.y;
      const dist = Math.hypot(dx, dy);
      const actualMoveX = chg.after.x - chg.before.x;
      const dropY = Math.max(0, chg.after.y - chg.before.y);

      // Only calibrate on clean flat-level unobstructed long slides so wall stops never bias Vmax downward!
      if (Math.abs(actualMoveX) > 160 && Math.abs(chg.after.y - chg.before.y) <= 2 && Math.abs(dx) > 6 && dist < cal.knockRadius - 6) {
        const falloff = 1 - dist / cal.knockRadius;
        const ux = dx / dist;
        const uy = dy / dist;
        const G = cal.bodyGravity || 1000;
        const curVmax = prev.type === 'bug' ? cal.bugVmax : cal.pushVmax;
        const kS = prev.type === 'bug' ? cal.bugSlideK : cal.pushSlideK;
        const vEst = curVmax * falloff;
        const vyEst = vEst * uy;
        const tAir = vyEst < 0 ? (-2 * vyEst) / G : 0;
        const predMoveX = (curVmax * falloff * ux) * (tAir + kS);
        if (Math.abs(predMoveX) > 100) {
          const ratio = Math.max(0.85, Math.min(1.15, actualMoveX / predMoveX));
          if (prev.type === 'bug') {
            cal.bugVmax = Math.max(760, Math.min(900, 0.8 * cal.bugVmax + 0.2 * (cal.bugVmax * ratio)));
            cal.bugSamples.push({ dist: +dist.toFixed(1), moveX: +actualMoveX.toFixed(1), predX: +predMoveX.toFixed(1) });
          } else {
            cal.pushVmax = Math.max(420, Math.min(640, 0.8 * cal.pushVmax + 0.2 * (cal.pushVmax * ratio)));
            cal.pushSamples.push({ dist: +dist.toFixed(1), moveX: +actualMoveX.toFixed(1), predX: +predMoveX.toFixed(1) });
          }
          updated = true;
        }
      }
    }
  }

  if (updated) saveCalibration(cal);
  return cal;
}
