import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { resolve, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { newOutputDirectory, writeJSON } from './runner.mjs';
import { verifyReplay } from './sim.mjs';
import { sha } from './maps.mjs';
const args = process.argv.slice(2), get = flag => args[args.indexOf(flag) + 1];
for (const flag of ['--out','--smoke','--calibration','--recovery']) if (!args.includes(flag) || !get(flag)) throw new Error('必需参数: --out 新目录 --smoke 目录 --calibration 目录 --recovery 目录');
const out = newOutputDirectory(get('--out'));
const tests = spawnSync(process.execPath, ['--test','experiments/golf-v1/*.test.mjs'], { encoding: 'utf8', windowsHide: true });
writeFileSync(join(out, 'tests.tap'), (tests.stdout ?? '') + (tests.stderr ?? ''), { flag: 'wx' });
if (tests.error || tests.status !== 0) throw tests.error ?? new Error('测试未通过，见tests.tap');
const replays = [];
for (const bot of ['naive','fitted']) for (const hole of ['tokens','bug']) {
  const path = join(get('--smoke'), bot + '-' + hole, 'replay.json');
  const sim = verifyReplay(JSON.parse(readFileSync(path, 'utf8')));
  const receipt = JSON.parse(readFileSync(join(get('--smoke'), bot + '-' + hole, 'receipt.json'), 'utf8'));
  if (!sim.completed || !receipt.cleanupVerified) throw new Error('smoke完成/清理证据无效');
  replays.push({ path, strokes: sim.strokes, penalties: sim.penalties, verified: true });
  const recoveryPath = join(get('--recovery'), bot + '-' + hole + '.json');
  const recovered = verifyReplay(JSON.parse(readFileSync(recoveryPath, 'utf8')));
  if (!recovered.completed) throw new Error('恢复场景未完成');
  replays.push({ path: recoveryPath, strokes: recovered.strokes, penalties: recovered.penalties, verified: true });
}
const report = JSON.parse(readFileSync(join(get('--calibration'), 'report.json'), 'utf8'));
if (report.fit.sampleCount > 12 || report.rows.length !== 60 || !report.passed.categories || !report.passed.fitted) throw new Error('校准证据缺失/无效');
const sourceRoot = fileURLToPath(new URL('./', import.meta.url)), files = [];
function collect(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) collect(path);
    else files.push({ path: relative(resolve('.'), path).replaceAll('\\','/'), sha256: sha(readFileSync(path)) });
  }
}
collect(sourceRoot);
const result = { engineeringOnly: true, testsPassed: true, testExitCode: tests.status, replays, calibrationAllPassed: report.allPassed,
  calibrationPassed: report.passed, files, limitations: '初始风切变校准未达标；完整细节和决策项见README。' };
writeJSON(join(out, 'validation.json'), result);
process.stdout.write(JSON.stringify({ testsPassed: true, replaysVerified: replays.length, calibrationAllPassed: report.allPassed }) + '\n');
