import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { GolfSim, verifyReplay } from './sim.mjs';
import { loadHole, sha } from './maps.mjs';
import { newOutputDirectory, writeJSON } from './runner.mjs';
import { trainFitted, localProbe, fittedFactor, chooseAction } from './bots/aim.mjs';
export async function recovery(directory) {
  const dir = newOutputDirectory(directory), fit = await trainFitted(localProbe), results = [];
  for (const bot of ['naive','fitted']) for (const kind of ['tokens','bug']) {
    const s = new GolfSim(loadHole(new URL('./holes/practice-' + kind + '.json', import.meta.url))), original = sha(s.grid);
    // 故意用掘进弹炸发球台中段，使后续瞄准面对破坏过的地形和倾角。
    s.execute({ attack: { kind: 'dig', angle: 85, power: 0, dir: 1 } });
    const afterMistake = sha(s.grid);
    while (!s.finished) s.execute(chooseAction(s.observe(), bot === 'fitted' ? fittedFactor(fit.parameters) : () => 1));
    verifyReplay(s.replay());
    const path = join(dir, bot + '-' + kind + '.json'); writeJSON(path, s.replay());
    results.push({ bot, hole: kind, terrainDamaged: original !== afterMistake, completed: s.completed, strokes: s.strokes, penalties: s.penalties, replay: path, replayVerified: true });
    if (!s.completed) throw new Error('首杆破坏后恢复失败');
  }
  writeJSON(join(dir, 'summary.json'), { fit, results, scope: '仅覆盖首杆在发球台中段打掘进弹这一破坏场景，不能证明任意破坏后都有解。' });
  return results;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const index = process.argv.indexOf('--out');
  if (index < 0 || !process.argv[index + 1]) throw new Error('用法: node recovery.mjs --out 新目录');
  process.stdout.write(JSON.stringify(await recovery(process.argv[index + 1])) + '\n');
}
