import { readFileSync } from 'node:fs';
import { verifyReplay } from './sim.mjs';
if (process.argv.length !== 3) throw new Error('用法: node verify-replay.mjs replay.json');
const replay = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const sim = verifyReplay(replay);
process.stdout.write(JSON.stringify({ verified: true, actions: sim.actions.length, completed: sim.completed, strokes: sim.strokes }) + '\n');
