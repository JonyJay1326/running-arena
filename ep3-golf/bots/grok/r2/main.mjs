import { createInterface } from 'node:readline';
import { answerExam, decide, resetHole } from './engine.mjs';

function safeFallback(msg) {
  if (msg.type === 'init') return { ok: true };
  if (msg.type === 'exam') return { predictions: [] };
  return { attack: { kind: 'single', angle: 70, power: 30, dir: 1 } };
}

function respond(msg) {
  if (msg.type === 'init') {
    resetHole();
    return { ok: true };
  }
  if (msg.type === 'exam') return answerExam(msg);
  if (msg.type === 'turn') return decide(msg);
  throw new Error(`未知消息类型：${msg.type}`);
}

for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (e) {
    process.stderr.write(`bad json ${e.message}\n`);
    continue;
  }
  let reply;
  try {
    reply = respond(msg);
  } catch (e) {
    process.stderr.write(`${e.stack || e}\n`);
    reply = safeFallback(msg);
  }
  process.stdout.write(JSON.stringify({ reqId: msg.reqId, ...reply }) + '\n');
}
