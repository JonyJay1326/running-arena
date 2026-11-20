import { createInterface } from 'node:readline';
import { chooseAction } from './aim.mjs';
for await (const line of createInterface({ input: process.stdin })) {
  const r = JSON.parse(line);
  const reply = r.type === 'init' ? { ok: true } : r.type === 'practice' ? { ready: true } : chooseAction(r);
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...reply }) + '\n');
}
