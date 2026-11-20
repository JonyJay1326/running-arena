import { mkdirSync, existsSync, writeFileSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startBot } from '../../runner/bot-process.mjs';
import { managedArena5Command, checkedProjectPath } from '../../runner/arena5-process.mjs';
import { GolfSim, publicConfig, verifyReplay } from './sim.mjs';
import { loadHole, flatMap } from './maps.mjs';
import { traceShot } from './physics.mjs';
import { CONFIG as C } from './config.mjs';

export function newOutputDirectory(directory) {
  const path = checkedProjectPath(directory);
  if (existsSync(path)) throw new Error('输出目录已存在，禁止覆盖');
  mkdirSync(dirname(path), { recursive: true }); mkdirSync(path);
  return path;
}
export const writeJSON = (path, value) => writeFileSync(path, JSON.stringify(value, null, 2), { flag: 'wx' });
function practiceShot(plan) {
  if (!plan || !plan.actor || !plan.aim || ![plan.actor.x, plan.actor.y, plan.actor.groundAngle, plan.wind, plan.aim.angle, plan.aim.power].every(Number.isFinite) ||
    plan.actor.x < 22 || plan.actor.x > 1578 || plan.actor.y !== 768 || plan.actor.groundAngle !== 0 || Math.abs(plan.wind) > 8 || !Number.isInteger(plan.wind * 2) ||
    plan.aim.angle < 0 || plan.aim.angle > 85 || plan.aim.power < 0 || plan.aim.power > 100 || ![-1, 1].includes(plan.aim.dir)) throw new Error('试射仅允许平地练习域');
  const p = traceShot(flatMap().grid, plan.actor, plan.wind, plan.aim);
  return { x: p.land.x, y: p.land.y, hit: p.land.hit };
}
export async function runHole({ hole, command, directory, practice = false, deadlineMs = 1000, initDeadlineMs = 5000, stopAt = Infinity }) {
  if (!Number.isFinite(deadlineMs) || deadlineMs <= 0 || deadlineMs > 1000 || !Number.isFinite(initDeadlineMs) || initDeadlineMs <= 0 || initDeadlineMs > 5000) throw new Error('非法期限');
  const dir = newOutputDirectory(directory), decisions = [], probes = [];
  let bot, managed, sim, failure; const cleanupErrors = []; let reqId = 0;
  const ask = async (request, limit) => {
    limit = Math.min(limit, stopAt - Date.now());
    if (limit <= 0) throw new Error('开发时间已结束');
    const message = { ...request, reqId: ++reqId, deadlineMs: limit };
    const result = await bot.ask(message, limit);
    decisions.push({ request: message, result });
    if (Date.now() >= stopAt) throw new Error('开发时间已结束');
    if (result.violation) throw new Error('Bot协议失败: ' + result.violation);
    const { reqId: ignored, ...response } = result.response;
    return response;
  };
  try {
    sim = new GolfSim(hole);
    managed = managedArena5Command(command, join(dir, 'process'));
    bot = startBot(managed);
    if (practice) {
      let lastProbe = null;
      for (;;) {
        const response = await ask({ type: 'practice', remaining: C.PRACTICE_PROBES - probes.length, lastProbe }, initDeadlineMs);
        if (response.ready === true) break;
        if (probes.length >= C.PRACTICE_PROBES || !response.prediction || ![response.prediction.x, response.prediction.y].every(Number.isFinite)) throw new Error('试射必须先预测，最多' + C.PRACTICE_PROBES + '次');
        lastProbe = practiceShot(response.probe);
        probes.push({ index: probes.length + 1, plan: response.probe, prediction: response.prediction, landing: lastProbe,
          predictionError: Math.hypot(lastProbe.x - response.prediction.x, lastProbe.y - response.prediction.y) });
      }
    }
    const init = await ask({ type: 'init', ...sim.observe(), config: publicConfig() }, initDeadlineMs);
    if (init.ok !== true || Object.keys(init).some(k => k !== 'ok')) throw new Error('Bot初始化响应无效');
    while (!sim.finished) {
      const action = await ask({ type: 'turn', ...sim.observe() }, deadlineMs);
      sim.execute(action);
    }
    verifyReplay(sim.replay());
  } catch (error) { failure = error; }
  if (bot) { try { await bot.close(); } catch (error) { cleanupErrors.push(error.message); } }
  else if (managed) { try { await managed.close(); } catch (error) { cleanupErrors.push(error.message); } }
  const receipt = { rulesVersion: 'golf-v1', engineeringOnly: true, status: failure || cleanupErrors.length ? 'failed' : 'completed',
    cleanupVerified: cleanupErrors.length === 0, cleanupErrors, error: failure?.message ?? null, completed: sim?.completed ?? false,
    strokes: sim?.strokes ?? null, penalties: sim?.penalties ?? null, probeCount: probes.length, replayVerified: !failure && cleanupErrors.length === 0 };
  writeJSON(join(dir, 'decisions.json'), decisions); writeJSON(join(dir, 'practice.json'), probes); writeJSON(join(dir, 'receipt.json'), receipt);
  if (failure || cleanupErrors.length) throw Object.assign(failure ?? new Error('进程树清理未确认'), { cleanupErrors, directory: dir });
  const replay = sim.replay(); writeJSON(join(dir, 'replay.json'), replay);
  return { sim, replay, receipt, directory: dir };
}
export function botCommand(name) {
  const scripts = { naive: 'bot-naive.mjs', fitted: 'bot-fitted.mjs', 'node-example': 'example.mjs', 'python-example': 'example.py' };
  if (!Object.hasOwn(scripts, name)) throw new Error('未知内置Bot');
  return { file: name === 'python-example' ? 'python' : process.execPath, args: [fileURLToPath(new URL('./bots/' + scripts[name], import.meta.url))], cwd: resolve('.') };
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2), get = flag => args[args.indexOf(flag) + 1];
  if (!args.includes('--hole') || !args.includes('--bot') || !args.includes('--out')) throw new Error('用法: node runner.mjs --hole 文件 --bot naive|fitted|node-example|python-example --out 新目录');
  const name = get('--bot');
  const result = await runHole({ hole: loadHole(get('--hole')), command: botCommand(name), directory: get('--out'), practice: name === 'fitted' });
  process.stdout.write(JSON.stringify(result.receipt) + '\n');
}
