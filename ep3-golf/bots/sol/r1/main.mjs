import { createInterface } from 'node:readline';
import { exam } from './geometry.mjs';
import { Planner } from './planner.mjs';

let planner;

function respond(r) {
  switch (r.type) {
    case 'init':
      planner = new Planner(r.config, r);
      return { ok: true };
    case 'turn':
      return planner.turn(r);
    case 'exam':
      return { predictions: r.questions.map(q => exam(q, r.ground, r.config)) };
    default:
      throw new Error('未知消息类型 ' + r.type);
  }
}

for await (const line of createInterface({ input: process.stdin })) {
  const r = JSON.parse(line);
  let reply;
  try {
    reply = respond(r);
  } catch (error) {
    process.stderr.write(String(error.stack ?? error) + '\n');
    if (r.type !== 'turn') throw error;
    reply = { attack: { kind: 'dig', angle: 42, power: 52, dir: r.actor.x > 800 ? -1 : 1 } };
  }
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
