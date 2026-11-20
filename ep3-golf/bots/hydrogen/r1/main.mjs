import { createInterface } from 'node:readline';
import { simulateFlatProjectile } from './physics.mjs';
import { planTurn } from './planner.mjs';

let initObs = null;
let initBugPos = null;
const history = [];

function answerExam(r) {
  const groundY = r.ground ?? 780;
  const muzzleHeight = r.config?.BODY?.muzzleHeight ?? 35;
  const predictions = r.questions.map(q => {
    const res = simulateFlatProjectile(q.x, groundY - muzzleHeight, q.angle, q.power, q.dir, q.wind, groundY, 1);
    return { id: q.id, x: Number(res.x.toFixed(2)), y: Number(res.y.toFixed(2)) };
  });
  return { predictions };
}

function recordLastTurn(obs) {
  if (!obs.lastResult || history.length === 0) return;
  const last = history[history.length - 1];
  const lr = obs.lastResult;
  const objMoved = lr.objectChanges && lr.objectChanges.length > 0;
  let craterDeepened = false;
  if (!objMoved && lr.impacts && lr.impacts[0] && history.length >= 2) {
    const prev = history[history.length - 2];
    if (prev.actualImpacts && prev.actualImpacts[0]) {
      const dImp = Math.hypot(lr.impacts[0].x - prev.actualImpacts[0].x, lr.impacts[0].y - prev.actualImpacts[0].y);
      if (dImp > 15) craterDeepened = true;
    }
  }
  const madeProgress =
    (objMoved || craterDeepened) &&
    (!lr.penalties || lr.penalties.length === 0);
  last.madeProgress = madeProgress;
  last.actualImpacts = lr.impacts;
}

function handleMessage(r) {
  switch (r.type) {
    case 'init': {
      initObs = r;
      const b = r.objects?.find(o => o.type === 'bug');
      if (b) initBugPos = { x: b.x, y: b.y };
      process.stderr.write(`init hole=${r.hole?.id} goal=${r.hole?.goal} wind=${r.hole?.wind} par=${r.hole?.par}\n`);
      return { ok: true };
    }
    case 'turn': {
      recordLastTurn(r);
      const obs = r.config ? r : { ...r, config: initObs?.config };
      const { command, meta } = planTurn(obs, history, initBugPos);
      history.push({
        stroke: r.strokes,
        kind: command.attack?.kind,
        angle: command.attack?.angle,
        power: command.attack?.power,
        dir: command.attack?.dir,
        moveX: command.moveX ?? r.actor.x,
        madeProgress: false,
      });
      process.stderr.write(
        `turn s=${r.strokes} cmd=${JSON.stringify(command)} meta=${JSON.stringify(meta)}\n`
      );
      return command;
    }
    case 'exam':
      return answerExam(r);
    default:
      throw new Error(`Unknown message type: ${r.type}`);
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  const r = JSON.parse(line);
  const reply = handleMessage(r);
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
