import { createInterface } from 'node:readline';
for await (const line of createInterface({ input: process.stdin })) {
  const r = JSON.parse(line);
  const action = r.type === 'init' ? { ok: true } : { attack: { kind: 'single', angle: 35, power: 50, dir: 1 } };
  process.stdout.write(JSON.stringify({ reqId: r.reqId, ...action }) + '\n');
}
