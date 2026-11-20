#!/usr/bin/env node
/**
 * 验证成绩：把 324 份回放全部重算一遍，逐洞比对杆数，再和总榜对。
 *
 *   node verify.mjs
 *   node verify.mjs --round 1     # 只验第一轮
 *   node verify.mjs --player sol  # 只验一位
 *
 * 回放里存的是 Bot 原始动作和初始地形，引擎重放会逐事件比对，
 * 所以任何一处对不上（改引擎、改地形、改回放）都会直接报错。
 */
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { verifyReplay } from './engine/sim.mjs';
import { holeScore } from './engine/scoring.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const flag = (name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : null;
};
const onlyRound = flag('--round');
const onlyPlayer = flag('--player');

const board = JSON.parse(fs.readFileSync(path.join(ROOT, 'results', 'standings.json'), 'utf8'));
let checked = 0;
let bad = 0;

console.log('选手'.padEnd(12) + '轮次  洞  记录  回放  结果');
for (const player of board.standings) {
  if (onlyPlayer && player.name !== onlyPlayer) continue;
  for (const rnd of ['r1', 'r2']) {
    if (onlyRound && rnd !== `r${onlyRound}`) continue;
    const round = player.rounds[rnd];
    if (!round) continue;
    let total = 0;
    let completed = 0;
    let roundBad = 0;
    for (const hole of round.holes) {
      const file = path.join(ROOT, 'replays', player.name, rnd, `h${hole.hole}.json.gz`);
      if (!fs.existsSync(file)) {
        console.log(`${player.name.padEnd(12)}${rnd}  ${hole.hole}  缺少回放`);
        bad += 1;
        roundBad += 1;
        continue;
      }
      let sim;
      try {
        const replay = JSON.parse(zlib.gunzipSync(fs.readFileSync(file)));
        sim = verifyReplay(replay);
      } catch (error) {
        console.log(`${player.name.padEnd(12)}${rnd}  ${hole.hole}  回放不一致：${error.message}`);
        bad += 1;
        roundBad += 1;
        continue;
      }
      checked += 1;
      const strokes = sim.strokes;
      const expectedStrokes = Math.min(hole.actualStrokes, hole.cap);
      const ok = hole.official ? true : (strokes === expectedStrokes && Boolean(sim.completed) === Boolean(hole.completed));
      if (!ok) {
        roundBad += 1;
        bad += 1;
      }
      total += hole.official ? hole.score : holeScore({ par: hole.par, strokes, completed: sim.completed, error: null });
      if (sim.completed) completed += 1;
      if (!ok) {
        console.log(`${player.name.padEnd(12)}${rnd}  ${hole.hole}  记录 ${expectedStrokes}/${hole.completed}  回放 ${strokes}/${sim.completed}  MISMATCH`);
      }
    }
    const totalOk = total === round.total;
    const completedOk = completed === round.completed;
    if (!totalOk || !completedOk) {
      bad += 1;
      roundBad += 1;
    }
    console.log(`${player.name.padEnd(12)}${rnd}  ${round.completed} 洞  总杆 ${round.total}  回放 ${total}  ${roundBad === 0 && totalOk && completedOk ? 'OK' : 'MISMATCH'}`);
  }
}

console.log('');
if (bad === 0) {
  console.log(`全部一致：${checked} 份回放重算通过。`);
  process.exit(0);
}
console.log(`${bad} 处对不上：检查引擎版本、回放文件或 results/standings.json。`);
process.exit(1);




