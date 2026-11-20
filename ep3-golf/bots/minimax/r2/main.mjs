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
// 磁盘笔记本只保留【可复用的物理参数】：爆炸推动 Bug 的模型（pushA/pushR/model/samples）。
// 不存任何按洞整理的状态、动作序列、坐标指纹或解法表；防复读用的动作历史只在内存里。
const LEARN_FILE = join(MEM, 'learn.json');
let learn = { pushA: 30, pushR: 34, samples: [] };
try {
  if (existsSync(LEARN_FILE)) {
    const raw = JSON.parse(readFileSync(LEARN_FILE, 'utf8'));
    // 只取物理参数，其余一律丢弃（防止历史状态混入）
    learn = {
      pushA: typeof raw.pushA === 'number' ? raw.pushA : 30,
      pushR: typeof raw.pushR === 'number' ? raw.pushR : 34,
      model: raw.model && typeof raw.model.A === 'number' ? raw.model : null,
      samples: Array.isArray(raw.samples)
        ? raw.samples.filter(s => typeof s.d === 'number' && typeof s.disp === 'number').slice(-80)
        : [],
    };
  }
} catch (e) { /* 记忆损坏则用默认值 */ }
function saveLearn() {
  try {
    if (!existsSync(MEM)) mkdirSync(MEM, { recursive: true });
    // 只写物理参数
    const out = {
      pushA: learn.pushA, pushR: learn.pushR, model: learn.model || null,
      samples: (learn.samples || []).slice(-80),
    };
    writeFileSync(LEARN_FILE, JSON.stringify(out));
  } catch (e) { /* 忽略 */ }
}

// 启动即建好 memory 目录，避免运行时写文件失败
try { if (!existsSync(MEM)) mkdirSync(MEM, { recursive: true }); } catch (e) {}

// ---------- 状态 ----------
let cfg = null;
let lastBugAim = null;   // 上一杆为推 Bug 选定的爆炸偏移（用于闭环修正）
// 最近的动作历史：用于「防复读」。连续几杆用完全相同的参数且毫无进展时，
// 说明这个解不可行（第一轮洞 06/07/08 连撞同一堵墙 4~6 杆），必须换策略。
let shotHistory = [];
let lastAction = null;   // Bot 自己发出的上一杆动作（用于防复读比对）

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
  // 新洞开始时清掉「当前洞」的临时状态（防复读历史、上一杆推 Bug 参数）。
  // 判据是杆数归零，不是洞号/地形指纹 —— 没有任何认洞逻辑。
  if ((obs.strokes || 0) === 0) {
    shotHistory = [];
    lastAction = null;
    lastBugAim = null;
    learn.bugLast = null;
  }
  learnFromLast(obs);

  const t0 = Date.now();
  let action = null;
  try {
    const model = bugPushModel();
    learn.model = model;
    // shotHistory 只在内存里用（防复读是「当前洞的失败次数」，属允许记录的运行时状态），
    // 不写进磁盘笔记本 —— 笔记本只保留可复用的物理参数（推力模型）。
    action = chooseShot(env, obs, { model, recent: shotHistory });
  } catch (e) {
    log('[turn] strategy error', e && e.stack);
  }
  if (!action || (!action.kind && !action.unplayable)) {
    action = { kind: 'single', angle: 35, power: 65, dir: 1 };
  }
  // 记录本杆的爆炸偏移，供下一杆闭环修正
  if (action.bugDd != null) {
    lastBugAim = { dd: action.bugDd, ux: action.bugUx || 1, uy: action.bugUy || 0, targetX: action.bugTargetX };
  }
  const reply = { reqId: msg.reqId };
  if (action.moveX != null && obs.hole.teeActive) reply.moveX = action.moveX;
  if (action.unplayable) reply.unplayable = true;
  else {
    reply.attack = { kind: action.kind, angle: action.angle, power: action.power, dir: action.dir };
    lastAction = reply.attack;
  }
  log('[turn] strokes', obs.strokes, 'goal', obs.hole.goal, '->', JSON.stringify(reply), (Date.now() - t0) + 'ms');
  return reply;
}

function learnFromLast(obs) {
  const lr = obs.lastResult;
  if (!lr || !lr.impacts || !lr.impacts.length) return;
  // --- 防复读：判断上一杆是否有实质进展 ---
  // 上一杆的动作由 Bot 自己记录（lastAction），不依赖观测里的字段结构。
  {
    const a = lastAction;
    let progress = false;
    for (const c of (lr.objectChanges || [])) {
      if (c.damage && c.damage > 0) progress = true;
      if (c.after && c.after.broken) progress = true;
      if (c.before && c.after && (c.before.x !== c.after.x || c.before.y !== c.after.y)) progress = true;
    }
    if (a) {
      shotHistory.push({ kind: a.kind, angle: a.angle, power: a.power, dir: a.dir, progress });
      if (shotHistory.length > 6) shotHistory.shift();
    }
  }
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
    // 沿期望方向的位移（二维：回收区可能在 Bug 的上方或下方）
    const moved = dx * lastBugAim.ux + dy * lastBugAim.uy;
    const over = moved > (lastBugAim.targetX != null
      ? Math.abs(lastBugAim.targetX - ch.before.x) + 20 : 1e9);
    learn.bugLast = { d: lastBugAim.dd, disp: Math.abs(moved), over, ux: lastBugAim.ux, uy: lastBugAim.uy };
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
