// 弹弹高尔夫 Bot 主入口：init / turn / exam 三类消息
import { createInterface } from 'node:readline';
import { HoleBrain } from './strategy.mjs';
import { predictFlat } from './physics.mjs';

let brain = null;
let initObs = null;

function handleInit(r) {
  initObs = r;
  try {
    brain = new HoleBrain(r);
    process.stderr.write(`init 洞=${r.hole.id} goal=${r.hole.goal} wind=${r.hole.wind} par=${r.hole.par}\n`);
  } catch (e) {
    process.stderr.write('init 异常：' + (e?.stack || e) + '\n');
  }
  return { ok: true };
}

function handleTurn(r) {
  try {
    if (!brain) {
      // 容错：没有 init 也能临时建
      brain = new HoleBrain(r);
    }
    const action = brain.decide(r);
    if (r.finished || r.completed) {
      brain.finish(r, !!r.completed);
    }
    return action;
  } catch (e) {
    process.stderr.write('turn 异常：' + (e?.stack || e) + '\n');
    // 兜底合法动作，避免协议错误结束本洞
    return { attack: { kind: 'single', angle: 45, power: 50, dir: 1 } };
  }
}

function handleExam(r) {
  // 考卷：平地预测，地面 y=780，题目自带 wind/x/angle/power/dir
  const ground = r.ground ?? 780;
  const predictions = [];
  for (const q of r.questions || []) {
    try {
      const out = predictFlat({
        x: q.x, angleDeg: q.angle, power: q.power, dir: q.dir ?? 1,
        wind: q.wind ?? 0, groundY: ground,
      });
      predictions.push({ id: q.id, x: +out.x.toFixed(2), y: +out.y.toFixed(2) });
    } catch (e) {
      process.stderr.write('exam 题异常 id=' + q.id + ' ' + e + '\n');
    }
  }
  return { predictions };
}

for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  let r;
  try {
    r = JSON.parse(line);
  } catch {
    continue;
  }
  let reply;
  try {
    if (r.type === 'init') reply = handleInit(r);
    else if (r.type === 'turn') reply = handleTurn(r);
    else if (r.type === 'exam') reply = handleExam(r);
    else throw new Error('未知消息类型：' + r.type);
  } catch (e) {
    process.stderr.write('处理异常：' + (e?.stack || e) + '\n');
    reply = r.type === 'exam' ? { predictions: [] } : { ok: false };
  }
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
