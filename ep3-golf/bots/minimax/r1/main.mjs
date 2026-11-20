// 弹弹高尔夫 Bot 主程序（Node 22，零依赖）。
// 协议：stdin 每行一个 JSON，stdout 每行一个 JSON。
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { MUZZLE_H, WIND_A, predictLanding, solveToPoint, surfaceY, decodeTerrain } from './physics.mjs';
import { makeEnv, chooseShot } from './strategy.mjs';

function surfaceYOf(obs, x) {
  const T = decodeTerrainCache(obs);
  const y = surfaceY(T, x);
  return y == null ? 780 : y;
}
let _terrainCache = null;
function decodeTerrainCache(obs) {
  if (!_terrainCache) _terrainCache = decodeTerrain(obs.terrain);
  return _terrainCache;
}

const __dir = dirname(fileURLToPath(import.meta.url));
const MEM = join(__dir, 'memory');

// ---------- 运行时记忆（跨洞） ----------
const LEARN_FILE = join(MEM, 'learn.json');
let learn = { pushA: 30, pushR: 34, samples: [] };
try {
  if (existsSync(LEARN_FILE)) learn = JSON.parse(readFileSync(LEARN_FILE, 'utf8'));
} catch (e) { /* 记忆损坏则用默认值 */ }
function saveLearn() {
  try {
    if (!existsSync(MEM)) mkdirSync(MEM, { recursive: true });
    writeFileSync(LEARN_FILE, JSON.stringify(learn));
  } catch (e) { /* 忽略 */ }
}

// 启动即建好 memory 目录，避免运行时写文件失败
try { if (!existsSync(MEM)) mkdirSync(MEM, { recursive: true }); } catch (e) {}

// ---------- 开发期实验钩子 ----------
// labcmd.json 支持两种形式：
//   {mode:"probe", offsets:[70,50,30,15], kind:"single"} —— 每杆在 Bug 左侧 offset 像素处落地爆炸
//   [action, action, ...] —— 直接按序执行的动作
const CMD_FILE = join(MEM, 'labcmd.json');
let cmdIdx = 0;
function readCmd() {
  try {
    if (!existsSync(CMD_FILE)) return null;
    return JSON.parse(readFileSync(CMD_FILE, 'utf8'));
  } catch (e) { return null; }
}

// ---------- 状态 ----------
let cfg = null;
let lastBugAim = null;   // 上一杆为推 Bug 选定的爆炸偏移（用于闭环修正）

function log(...a) {
  try { process.stderr.write(a.join(' ') + '\n'); } catch (e) {}
}

// ---------- 学习：爆炸对 bug 的推动（距离 -> 位移） ----------
// 模型：位移 = A * max(0, R - d)，d = 爆炸点到 Bug 中心的距离。
function bugPushModel() {
  const s = learn.samples || [];
  if (s.length < 2) return { A: learn.pushA || 35, R: learn.pushR || 34 };
  let best = { A: learn.pushA || 35, R: learn.pushR || 34, err: Infinity };
  for (let A = 2; A <= 120; A += 0.5) {
    for (let R = 12; R <= 140; R += 0.5) {
      let e = 0;
      for (const p of s) {
        const pred = p.d < R ? A * (R - p.d) : 0;
        e += (pred - p.disp) ** 2;
      }
      if (e < best.err) best = { A, R, err: e };
    }
  }
  learn.pushA = best.A; learn.pushR = best.R;
  return { A: best.A, R: best.R };
}

// ---------- 协议处理 ----------
function answerInit(msg) {
  cfg = msg.config || cfg;
  log('[init] hole', msg.hole && msg.hole.id, 'goal', msg.hole && msg.hole.goal);
  return { reqId: msg.reqId, ok: true };
}

function answerExam(msg) {
  const cfgE = msg.config || cfg || {};
  const ground = msg.ground == null ? 780 : msg.ground;
  const up = (cfgE.BODY && cfgE.BODY.muzzleHeight) || MUZZLE_H;
  const out = [];
  for (const q of msg.questions || []) {
    const p = predictLanding(q.x, ground, q.angle, q.power, q.dir, q.wind, up);
    out.push({ id: q.id, x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10 });
  }
  log('[exam]', JSON.stringify(out));
  return { reqId: msg.reqId, predictions: out };
}

function answerTurn(msg) {
  const obs = msg;
  const env = makeEnv(obs);
  learnFromLast(obs);

  // 开发期实验钩子：bot/memory/labcmd.json
  const cmd = readCmd();
  if (cmd && cmd.mode === 'probe') {
    const bug = obs.objects.find(o => o.type === 'bug');
    const step = (cmd.steps && cmd.steps[cmdIdx]) || {};
    const off = step.off != null ? step.off : ((cmd.offsets && cmd.offsets[cmdIdx]) || 20);
    const side = step.side || ((cmd.sides && cmd.sides[cmdIdx]) || 'l');
    const direct = step.direct || (cmd.direct === true && cmdIdx === 0);
    const sign = side === 'r' ? 1 : -1;
    const tx = bug ? (direct ? bug.x : bug.x + sign * off) : 700;
    const ty = bug ? (direct ? bug.y - (bug.radius || 14) : (surfaceYOf(obs, tx) || 780)) : 780;
    const dir = tx >= obs.actor.x ? 1 : -1;
    const sol = solveToPoint(obs.actor.x, obs.actor.y + env.footOffset - env.muzzleHeight,
      tx, ty, dir, WIND_A * (obs.hole.wind || 0), 0, { step: 0.5 })[0];
    cmdIdx++;
    const kind = step.kind || cmd.kind || 'single';
    const reply = { reqId: msg.reqId };
    if (sol) reply.attack = { kind, angle: Math.round(sol.angle * 10) / 10, power: Math.round(sol.power * 10) / 10, dir };
    else reply.attack = { kind: 'single', angle: 40, power: 70, dir };
    log('[probe] step', JSON.stringify(step), 'target', Math.round(tx), Math.round(ty), JSON.stringify(reply.attack));
    return reply;
  }
  if (Array.isArray(cmd) && cmd.length) {
    const i = (cmdIdx | 0);
    cmdIdx = i + 1;
    const a = cmd[i];
    const reply = { reqId: msg.reqId };
    if (a) {
      if (a.moveX != null && obs.hole.teeActive) reply.moveX = a.moveX;
      if (a.unplayable) reply.unplayable = true;
      else if (a.fly) reply.fly = a.fly;
      else reply.attack = a.attack || a;
    }
    log('[cmd] idx', i, JSON.stringify(reply));
    return reply;
  }

  const t0 = Date.now();
  let action = null;
  try {
    const model = bugPushModel();
    learn.model = model;
    action = chooseShot(env, obs, learn);
  } catch (e) {
    log('[turn] strategy error', e && e.stack);
  }
  if (!action || (!action.kind && !action.unplayable)) {
    action = { kind: 'single', angle: 35, power: 65, dir: 1 };
  }
  // 记录本杆的爆炸偏移，供下一杆闭环修正
  if (action.bugDd != null) lastBugAim = { dd: action.bugDd, dirx: action.bugDirx || 1, targetX: action.bugTargetX };
  const reply = { reqId: msg.reqId };
  if (action.moveX != null && obs.hole.teeActive) reply.moveX = action.moveX;
  if (action.unplayable) reply.unplayable = true;
  else reply.attack = { kind: action.kind, angle: action.angle, power: action.power, dir: action.dir };
  log('[turn] strokes', obs.strokes, 'goal', obs.hole.goal, '->', JSON.stringify(reply), (Date.now() - t0) + 'ms');
  return reply;
}

function learnFromLast(obs) {
  const lr = obs.lastResult;
  if (!lr || !lr.impacts || !lr.impacts.length) return;
  const bug = obs.objects.find(o => o.type === 'bug');
  if (!bug || !lr.objectChanges) return;
  const ch = lr.objectChanges.find(c => c.id === bug.id);
  if (!ch || !ch.before || !ch.after) return;
  const dx = ch.after.x - ch.before.x, dy = ch.after.y - ch.before.y;
  const disp = Math.hypot(dx, dy);
  if (disp < 0.5) return;
  // 爆炸点：用离 bug「爆炸前」位置最近的那个 impact
  const bc = { x: ch.before.x, y: ch.before.y - (bug.radius || 14) };
  let bd = Infinity;
  for (const c of lr.impacts) {
    const d = Math.hypot(c.x - bc.x, c.y - bc.y);
    if (d < bd) bd = d;
  }
  learn.samples = learn.samples || [];
  learn.samples.push({ d: Math.round(bd * 10) / 10, disp: Math.round(disp * 10) / 10 });
  if (learn.samples.length > 80) learn.samples.shift();
  bugPushModel();
  // 闭环：记录本杆实际位移与是否过冲，供下一杆调整爆炸距离
  if (lastBugAim) {
    const moved = dx * lastBugAim.dirx;   // 沿期望方向的位移
    const over = moved > (lastBugAim.targetX != null
      ? Math.abs(lastBugAim.targetX - ch.before.x) + 20 : 1e9);
    learn.bugLast = { d: lastBugAim.dd, disp: Math.abs(moved), over, dirx: lastBugAim.dirx };
    lastBugAim = null;
  }
  saveLearn();
  log('[learn] push d=', bd.toFixed(1), 'disp=', disp.toFixed(1), 'bugLast', JSON.stringify(learn.bugLast || null));
}
// ---------- 主循环 ----------
let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (line) handle(line);
  }
});
process.stdin.on('end', () => { process.exit(0); });

function handle(line) {
  let msg;
  try { msg = JSON.parse(line); } catch (e) { return; }
  let reply = null;
  try {
    if (msg.type === 'init') reply = answerInit(msg);
    else if (msg.type === 'exam') reply = answerExam(msg);
    else if (msg.type === 'turn') reply = answerTurn(msg);
    else reply = { reqId: msg.reqId, ok: true };
  } catch (e) {
    log('[error]', e && e.stack);
    if (msg && msg.reqId != null) {
      if (msg.type === 'turn') reply = { reqId: msg.reqId, attack: { kind: 'single', angle: 35, power: 60, dir: 1 } };
      else reply = { reqId: msg.reqId, ok: true };
    }
  }
  if (reply) process.stdout.write(JSON.stringify(reply) + '\n');
}
