// bot/main.mjs — 弹弹高尔夫正式参赛 Bot 入口（支持 init / turn / exam 协议）
import { createInterface } from 'node:readline';
import { loadCalibration, updateCalibrationFromResult } from './memory.mjs';
import { predictExamQuestion } from './physics.mjs';
import { decideTurnAction } from './strategy.mjs';

let holeConfig = null;
let calibration = loadCalibration();
let holeState = {
  holeId: null,
  initialBug: null,
  prevObjects: null,
  lastPredictedImpacts: null,
  stalledPoints: [],
  consecutiveStalls: 0,
};

function handleInit(msg) {
  holeConfig = msg.config || {};
  calibration = loadCalibration();
  const bug = (msg.objects || []).find(o => o.type === 'bug');
  holeState = {
    holeId: msg.hole?.id ?? null,
    initialBug: bug ? { x: bug.x, y: bug.y } : null,
    prevObjects: msg.objects ? msg.objects.map(o => ({ ...o })) : [],
    lastPredictedImpacts: null,
    stalledPoints: [],
    consecutiveStalls: 0,
  };
  const objsDesc = (msg.objects || [])
    .map(o => `#${o.id}:${o.type}@(${Math.round(o.x)},${Math.round(o.y)})${o.hp != null ? '/hp' + o.hp : ''}`)
    .join(' ');
  process.stderr.write(
    `[init] hole=${msg.hole?.id} goal=${msg.hole?.goal} wind=${msg.hole?.wind} par=${msg.hole?.par} fly=${msg.fly} tee=[${msg.hole?.tee?.x0},${msg.hole?.tee?.x1}] recycle=${JSON.stringify(msg.hole?.recycle)} objs=[${objsDesc}]\n`
  );
  return { ok: true };
}

function recordFeedback(obs) {
  const lr = obs.lastResult;
  if (!lr) return;

  // Update empirical knockback calibration
  calibration = updateCalibrationFromResult(calibration, lr, holeState.prevObjects, holeConfig);

  // Check prediction accuracy vs actual impacts
  if (holeState.lastPredictedImpacts && Array.isArray(lr.impacts) && lr.impacts.length > 0) {
    const p0 = holeState.lastPredictedImpacts[0];
    const a0 = lr.impacts[0];
    if (p0 && a0) {
      process.stderr.write(
        `[feedback] pred0=(${p0.x.toFixed(2)},${p0.y.toFixed(2)}) actual0=(${a0.x.toFixed(2)},${a0.y.toFixed(2)}) err=(${(a0.x - p0.x).toFixed(2)},${(a0.y - p0.y).toFixed(2)})\n`
      );
    }
  }

  // Detect stalled shot (no object change and repeated impact near previous stalled impact)
  const hadObjChange = Array.isArray(lr.objectChanges) && lr.objectChanges.some(
    c => (c.damage && c.damage > 0) || (c.before && c.after && Math.hypot(c.after.x - c.before.x, c.after.y - c.before.y) > 4)
  );
  if (!hadObjChange && Array.isArray(lr.impacts) && lr.impacts.length > 0) {
    holeState.consecutiveStalls++;
    if (holeState.consecutiveStalls >= 2) {
      for (const imp of lr.impacts) {
        holeState.stalledPoints.push({ x: imp.x, y: imp.y });
      }
    }
  } else {
    holeState.consecutiveStalls = 0;
  }
}

function handleTurn(obs) {
  try {
    recordFeedback(obs);
    const { action, predictedImpacts, debug } = decideTurnAction(obs, holeState, holeConfig, calibration);
    holeState.prevObjects = obs.objects ? obs.objects.map(o => ({ ...o })) : [];
    holeState.lastPredictedImpacts = predictedImpacts || null;

    process.stderr.write(
      `[turn s=${obs.strokes}] actor=(${obs.actor?.x?.toFixed?.(1)},${obs.actor?.y?.toFixed?.(1)},ang=${obs.actor?.groundAngle?.toFixed?.(2)}) -> ${JSON.stringify(action)} | ${JSON.stringify(debug)}\n`
    );
    return action;
  } catch (err) {
    process.stderr.write(`[turn error fallback] ${err?.stack || err}\n`);
    const dir = (obs.actor?.x ?? 400) < 800 ? 1 : -1;
    return { attack: { kind: 'single', angle: 45, power: 60, dir } };
  }
}

function handleExam(msg) {
  const cfg = msg.config || {};
  const ground = msg.ground ?? 780;
  const predictions = (msg.questions || []).map(q => predictExamQuestion(q, ground, cfg));
  return { predictions };
}

function respond(r) {
  switch (r.type) {
    case 'init':
      return handleInit(r);
    case 'turn':
      return handleTurn(r);
    case 'exam':
      return handleExam(r);
    default:
      throw new Error(`未知消息类型：${r.type}`);
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  const r = JSON.parse(line);
  const reply = respond(r);
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
