"""无头自测 CLI：python engine/evaluator.py bots/bot_red.py [--mode ice|moon] [--time 30]

以远超实时的速度跑完整场比赛，并输出语义化的“体检报告”。（只读，参赛 AI 不得修改）
"""
import argparse
import ast
import builtins
import hashlib
import json
import math
import os
import random
import sys
import time
import traceback
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import physics  # noqa: E402

ALLOWED_IMPORTS = {"math", "random"}
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "input", "breakpoint", "memoryview", "exit", "quit",
}

# 姿态阈值（m / rad / m·s⁻¹）
Y_STAND = 0.9
Y_DOWN = 0.45               # 躯干中心低于此高度算倒地
LEAN_DOWN = math.radians(50)  # 躯干倾角（前倾或后仰）达到 50° ……
Y_LEAN_DOWN = 0.70          # …… 且躯干中心低于此高度，也算倒地（深蹲/直立步态的躯干中心都在 0.9m 以上）
Y_FLY = 1.41                # 站立躯干中心高度约 1.28m，再高 0.13m 视为腾空


class BotError(Exception):
    pass


def is_down(obs):
    """倒地判定（计分、报告、回放标签共用这一个定义）：躯干/头触地；或躯干中心高度 < Y_DOWN；
    或 |躯干倾角| >= LEAN_DOWN 且躯干中心高度 < Y_LEAN_DOWN。"""
    if obs["contact"]["torso"] or obs["y"] < Y_DOWN:
        return True
    ang = (obs["torso_angle"] + math.pi) % (2 * math.pi) - math.pi
    return abs(ang) >= LEAN_DOWN and obs["y"] < Y_LEAN_DOWN


def down_rule_text():
    return f"躯干/头触地、躯干中心高度 < {Y_DOWN}m、或躯干倾角达到 {round(math.degrees(LEAN_DOWN))}° 且躯干中心高度 < {Y_LEAN_DOWN}m"


# ------------------------------------------------------------ 载入选手 ----
def _check_source(src, path):
    tree = ast.parse(src, filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [(node.module or "").split(".")[0]]
        else:
            mods = []
        bad = [m for m in mods if m not in ALLOWED_IMPORTS]
        if bad:
            raise BotError(f"第 {node.lineno} 行：禁止导入 {bad}，只允许 {sorted(ALLOWED_IMPORTS)}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise BotError(f"第 {node.lineno} 行：禁止使用 {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise BotError(f"第 {node.lineno} 行：禁止访问下划线属性 .{node.attr}")


def load_bot(path):
    """每次调用都返回全新的 act 函数（模块级全局状态被重置）。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    _check_source(src, path)
    safe_builtins = {k: v for k, v in vars(builtins).items() if k not in FORBIDDEN_NAMES}
    safe_builtins["__import__"] = _safe_import
    ns = {"__builtins__": safe_builtins, "__name__": "bot"}
    exec(compile(src, path, "exec"), ns)
    act = ns.get("act")
    if not callable(act):
        raise BotError("文件中没有找到 act(obs) 函数")
    return act


def _safe_import(name, *args, **kwargs):
    if name.split(".")[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"禁止导入 {name}")
    return __import__(name, *args, **kwargs)


# ------------------------------------------------------------ 模拟 --------
def simulate(act, mode="normal", sim_time=physics.SIM_TIME, track=physics.DEFAULT_TRACK, on_step=None, start=None):
    """跑完整场比赛，返回逐帧记录。可视化器复用同一函数逻辑，保证结果一致。

    on_step: 可选回调 on_step(runner, obs)，每次 runner.step(action) 之后调用（obs 为该步之前的观测），
    供 engine/visualizer.py 记录逐帧姿态；不影响模拟结果。
    """
    random.seed(20260101)
    runner = physics.Runner(mode, track, start)
    frames = []
    errors = []
    last_action = list(physics.REST_ACTION)
    finish_time = None
    lost, prev_x = 0.0, 0.0     # 倒地（见 is_down）期间的水平位移一律不计入成绩（爬行流判罚）
    for _ in range(int(round(sim_time * physics.FPS))):
        obs = runner.observe()
        try:
            action = act(_copy_obs(obs))
            action = [float(v) for v in action]
            if len(action) == 4:        # 只控制腿：手臂自然下垂
                action += physics.REST_ACTION[4:]
            if len(action) != physics.N_ACTIONS:
                raise ValueError(f"act 必须返回 {physics.N_ACTIONS} 个数，实际返回 {len(action)} 个")
            last_action = action
        except Exception:  # noqa: BLE001 — 选手代码可能抛任何异常
            if len(errors) < 3:
                errors.append((obs["time"], traceback.format_exc(limit=3)))
            elif len(errors) == 3:
                errors.append((obs["time"], "……后续错误已省略"))
            action = last_action
        if is_down(obs):
            lost += obs["x"] - prev_x
        prev_x = obs["x"]
        obs["score"] = obs["x"] - max(lost, 0.0)      # 成绩不超过实际位置（倒地后往回滑不能加分）
        runner.step(action)
        if on_step is not None:
            on_step(runner, obs)
        obs["action"] = [max(-1.0, min(1.0, a)) for a in action]
        frames.append(obs)
        if finish_time is None and obs["score"] >= physics.TRACK_LENGTH:
            finish_time = obs["time"]
    final = runner.observe()
    if is_down(final):
        lost += final["x"] - prev_x
    lost = max(lost, 0.0)       # 倒地期间往回滑产生的负值不计，成绩不会超过实际位置
    score = final["x"] - lost
    if finish_time is None and score >= physics.TRACK_LENGTH:     # 最后一步才越线：逐帧检查看不到最后一次观测
        finish_time = final["time"]
    return {"frames": frames, "final": final, "score": score, "lost": lost, "errors": errors, "finish_time": finish_time, "mode": mode, "track": track}


def _copy_obs(obs):
    o = dict(obs)
    o["joints"] = list(obs["joints"])
    o["contact"] = dict(obs["contact"])
    return o


# ------------------------------------------------------------ 诊断 --------
LABELS = {
    # key: (显示名, 权重：越罕见越值得报告)
    "split": ("💀 一字马卡死", 6.0),
    "back": ("🚨 后脑勺着地", 3.0),
    "faceslide": ("💥 脸刹滑行", 4.0),
    "face": ("🙇 五体投地", 3.0),
    "fly": ("🚀 超级起飞", 5.0),
    "crawl": ("🐛 蠕动前行", 2.0),
    "struggle": ("🪳 倒地挣扎", 1.5),
    "retreat": ("🦀 倒车", 1.5),
    "sitback": ("🦆 仰天坐姿蹦跶", 2.5),
    "hunch": ("🦍 猩猩弓背", 2.5),
    "unbalanced": ("⚠️ 失去平衡", 2.0),
    "walk": ("🏃 正常迈步", 1.0),
    "idle": ("🧍 原地罚站", 1.0),
}


def classify(frames):
    """逐帧打标签。"""
    labels = []
    split_frames = 0
    for f in frames:
        hl, hr = f["joints"][0], f["joints"][2]
        c = f["contact"]
        y, vx, ang = f["y"], f["vx"], f["torso_angle"]
        if abs(hl - hr) > 1.6 and y < 0.75 and abs(vx) < 0.1:
            split_frames += 1
        else:
            split_frames = 0
        if split_frames > 1.5 * physics.FPS:
            lab = "split"
        elif c["torso"] and ang < -0.8:
            lab = "back"
        elif c["torso"] and ang > 0.8:
            lab = "faceslide" if vx > 0.5 else "face"
        elif not c["foot_L"] and not c["foot_R"] and y > Y_FLY:
            lab = "fly"
        elif is_down(f):            # 与计分同一个倒地定义（含"低位大倾角"）
            lab = "crawl" if vx > 0.15 else "struggle"
        elif abs(ang) > 0.6 and y < Y_STAND:
            lab = "sitback" if ang < 0 else "hunch"
        elif abs(ang) > 0.6:
            lab = "unbalanced"
        elif vx < -0.3:
            lab = "retreat"
        elif y >= Y_STAND and vx > 0.3:
            lab = "walk"
        else:
            lab = "idle"
        labels.append(lab)
    return labels


def _second_label(labs):
    cnt = Counter(labs)
    return max(cnt, key=lambda k: cnt[k] * LABELS[k][1])


def _describe(lab, seg):
    dx, ang = seg["dx"], seg["ang"]
    f0, f1 = seg["first"], seg["last"]
    deg = round(ang * 57.3)
    if lab == "walk":
        return f"前进 {dx:+.2f}m | 躯干基本直立 (平均倾角 {deg:+d}°)"
    if lab == "idle":
        return f"前进 {dx:+.2f}m | 站着发呆，几乎没动 (平均倾角 {deg:+d}°)"
    if lab == "unbalanced":
        side = "前扑" if ang > 0 else "后仰"
        return f"躯干急速{side}，倾角恶化至 {round(seg['ang_ext'] * 57.3):+d}°，前进 {dx:+.2f}m"
    if lab == "sitback":
        return f"上半身后仰 {deg:+d}° 坐在自己腿上蹦跶，位移 {dx:+.2f}m"
    if lab == "hunch":
        return f"上半身前弓 {deg:+d}° 弯腰驼背地挪动，位移 {dx:+.2f}m"
    if lab == "back":
        return f"躯干触地！四脚朝天在地上空蹬双腿，位移 {dx:+.2f}m"
    if lab == "face":
        return f"脸朝下趴在地上，位移 {dx:+.2f}m"
    if lab == "faceslide":
        return f"用脸当滑板向前滑了 {dx:+.2f}m"
    if lab == "fly":
        return f"双脚离地！躯干最高 {seg['y_max']:.2f}m，飞行 {dx:+.2f}m"
    if lab == "crawl":
        return f"倒地状态下靠四肢磕地蹭行了 {dx:+.2f}m"
    if lab == "struggle":
        return f"倒在地上原地扑腾，位移 {dx:+.2f}m"
    if lab == "retreat":
        return f"一路倒车 {dx:+.2f}m" if dx < 0 else f"前后来回晃荡，净位移 {dx:+.2f}m"
    if lab == "split":
        return f"双髋处于极限相反角度，劈叉卡死在 x={f1['x']:.2f}m 无法动弹"
    return f"位移 {dx:+.2f}m"


def segments(frames, labels):
    fps = physics.FPS
    per_sec = []
    for s in range(0, len(frames), fps):
        per_sec.append((s // fps, _second_label(labels[s:s + fps])))
    segs = []
    for sec, lab in per_sec:
        if segs and segs[-1]["lab"] == lab:
            segs[-1]["end"] = sec + 1
        else:
            segs.append({"lab": lab, "start": sec, "end": sec + 1})
    for seg in segs:
        fs = frames[seg["start"] * fps: seg["end"] * fps]
        nxt = frames[min(seg["end"] * fps, len(frames) - 1)]
        seg["first"], seg["last"] = fs[0], fs[-1]
        seg["dx"] = nxt["x"] - fs[0]["x"]
        seg["ang"] = sum(f["torso_angle"] for f in fs) / len(fs)
        seg["ang_ext"] = max((f["torso_angle"] for f in fs), key=abs)
        seg["y_max"] = max(f["y"] for f in fs)
    return segs


def hints(frames, labels, result):
    out = []
    fps = physics.FPS
    first_back = next((f for f in frames if f["torso_angle"] < -0.5), None)
    first_front = next((f for f in frames if f["torso_angle"] > 0.5), None)
    if first_back and (not first_front or first_back["time"] <= first_front["time"]):
        out.append(f"t={first_back['time']:.1f}s 时 torso_angle < -0.5 (严重后仰)。")
    elif first_front:
        out.append(f"t={first_front['time']:.1f}s 时 torso_angle > 0.5 (严重前扑)。")
    down = [f for f in frames if is_down(f)]
    if len(down) > 3 * fps:
        out.append(f"共有 {len(down) / fps:.1f}s 处于倒地状态（{down_rule_text()}）。")
    stuck = _stuck_at_obstacle(frames, result.get('track', physics.DEFAULT_TRACK))
    if stuck:
        out.append(f"t={stuck[0]:.1f}s 起在 x={stuck[1]:.2f}m 的{stuck[2]}前卡住超过 2 秒。")
    air = sum(1 for f in frames if not f["contact"]["foot_L"] and not f["contact"]["foot_R"]) / len(frames)
    if air > 0.35:
        out.append(f"腾空时间占比 {air:.0%}。")
    if labels.count("split") > fps:
        out.append("检测到长时间一字马：两侧 hip 指令相差太大且没有收回。")
    if result["errors"]:
        out.insert(0, "你的 act() 抛出了异常（出错帧沿用上一帧动作），请先修复下方报错！")
    if not out:
        out.append("没有触发任何关键事件。")
    return out


def _stuck_at_obstacle(frames, track=physics.DEFAULT_TRACK):
    fps = physics.FPS
    obs = physics.obstacles(track)
    for i in range(0, len(frames) - 2 * fps, fps // 2):
        a, b = frames[i], frames[i + 2 * fps]
        if abs(b["x"] - a["x"]) < 0.1:
            for ox, _h, kind in obs:
                if 0 < ox - a["x"] < 0.6:
                    return a["time"], ox, "减速带" if kind == "bump" else "跨栏"
    return None


GRADES = [(physics.TRACK_LENGTH, "S", "冲线封神"), (12, "A", "健步如飞"), (8, "B", "像个人了"),
          (4, "C", "勉强及格"), (1, "D", "惨不忍睹"), (-1e9, "E", "原地去世")]


def report(result, path, wall):
    frames = result["frames"]
    labels = classify(frames)
    segs = segments(frames, labels)
    fin = result["final"]
    dist = result["score"]
    grade = next((g, t) for th, g, t in GRADES if dist >= th)
    vmax = max(f["vx"] for f in frames)
    hits = sum(1 for a, b in zip(frames, frames[1:]) if b["contact"]["torso"] and not a["contact"]["torso"])
    air = sum(1 for f in frames if not f["contact"]["foot_L"] and not f["contact"]["foot_R"]) / len(frames)
    sim_t = len(frames) / physics.FPS

    L = []
    L.append(f"================ 🏃 QWOP 模拟自测报告 ({os.path.basename(path)}, 赛道: {physics.TRACKS[result.get('track', physics.DEFAULT_TRACK)]['label']}, 模式: {result['mode']}, 耗时 {wall:.2f}秒) ================")
    finish = f" | 🏁 {result['finish_time']:.2f}s 冲过 {physics.TRACK_LENGTH:.0f}m 终点!" if result["finish_time"] else ""
    L.append(f"[最终成绩] 模拟时长: {sim_t:.1f}s | 最终前进距离: {dist:.2f} 米 | 综合评级: {grade[0]} ({grade[1]}){finish}")
    if abs(result["lost"]) > 0.05:
        L.append(f"[爬行判罚] 倒地状态（{down_rule_text()}）下水平位移 {result['lost']:+.2f} 米，这部分不计入成绩（实际位置 {fin['x']:.2f}m，计分 {dist:.2f}m）")
    L.append(f"[身体统计] 最高速度: {vmax:.2f} m/s | 躯干砸地次数: {hits} 次 | 腾空时间占比: {air:.0%} | 最终躯干高度: {fin['y']:.2f}m")
    L.append(f"[赛道进度] {_progress(dist, result.get('track', physics.DEFAULT_TRACK))}")
    L.append("")
    L.append("[⏱️ 逐秒姿态回放与死因诊断]")
    for seg in segs:
        name = LABELS[seg["lab"]][0]
        L.append(f"- {seg['start']:02d}s ~ {seg['end']:02d}s: [{name}] {_describe(seg['lab'], seg)}")
    L.append("")
    L.append("[📌 关键事件]")
    for i, h in enumerate(hints(frames, labels, result), 1):
        L.append(f"{i}. {h}")
    if result["errors"]:
        L.append("")
        L.append("[🐞 act() 报错]")
        for t, tb in result["errors"]:
            L.append(f"t={t:.2f}s:\n{tb.rstrip()}")
    L.append("=" * 72)
    return "\n".join(L)


def _progress(dist, track=physics.DEFAULT_TRACK):
    here = [name for x, name in physics.TRACKS[track]["zones"] if dist >= x]
    return f"{dist:.2f}m / {physics.TRACK_LENGTH:.0f}m，当前处于【{here[-1] if here else '起跑线后方'}】区域"


# ------------------------------------------------------------ 起身测试 ----
GETUP_TIME = 20.0       # 每个起点的时间预算 (s)
GETUP_HOLD = 2.0        # 站起后需保持站立的时间 (s)
GETUP_Y = 1.06          # 站稳所需躯干中心高度 (m)，约为静止站立高度 1.28m 的 0.83
GETUP_FULL_Y = 1.28     # 没站稳时按"躯干最高 / 站立高度"给 0~5 分


def getup_eval(frames):
    """站稳判据：躯干高度 >= GETUP_Y、|躯干倾角| < 0.6、至少一只脚着地，并连续保持 GETUP_HOLD 秒。"""
    run, t_stand, max_y = 0, None, 0.0
    for f in frames:
        max_y = max(max_y, f["y"])
        ok = f["y"] >= GETUP_Y and abs(f["torso_angle"]) < 0.6 and (f["contact"]["foot_L"] or f["contact"]["foot_R"])
        run = run + 1 if ok else 0
        if t_stand is None and run >= int(GETUP_HOLD * physics.FPS):
            t_stand = f["time"] - GETUP_HOLD
    pts = (25.0 - t_stand) if t_stand is not None else 5.0 * min(1.0, max_y / GETUP_FULL_Y)
    return {"t_stand": t_stand, "max_y": max_y, "pts": max(0.0, pts)}


def getup_report(act, path, mode="normal"):
    """从"面朝下趴着"和"仰躺"两个起点各跑一次，返回 (总分, 报告文本)。"""
    out, total = [], 0.0
    names = {"prone": "面朝下趴着", "supine": "仰躺（后脑勺着地）"}
    for start in ("prone", "supine"):
        res = simulate(act, mode, GETUP_TIME, "flat", start=start)
        g = getup_eval(res["frames"])
        total += g["pts"]
        frames = res["frames"]
        out.append(f"--- 起点：{names[start]} ---")
        if g["t_stand"] is not None:
            out.append(f"✅ {g['t_stand']:.2f}s 站起并稳住 {GETUP_HOLD:.0f}s（躯干高度 ≥{GETUP_Y}m、倾角 <0.6、至少一脚着地）| 本起点得分 {g['pts']:.1f}")
        else:
            out.append(f"❌ {GETUP_TIME:.0f}s 内没有站稳 | 期间躯干最高 {g['max_y']:.2f}m（站稳要求 ≥{GETUP_Y}m）| 本起点得分 {g['pts']:.1f}")
        line = []
        for s in range(0, int(GETUP_TIME), 2):
            f = frames[min(len(frames) - 1, s * physics.FPS)]
            line.append(f"{s}s: y={f['y']:.2f} 倾角={f['torso_angle']:+.2f}")
        out.append("  " + " | ".join(line))
        if res["errors"]:
            out.append("🐞 act() 报错：" + res["errors"][0][1].strip().splitlines()[-1])
    head = (f"================ 🧗 起身测试自测报告 ({os.path.basename(path)}) ================\n"
            f"[总分] {total:.1f} / 50（站起越快分越高；没站起按躯干最高度给 0~5 分）\n")
    return total, head + "\n".join(out) + "\n" + "=" * 72


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEST_FILE = os.path.join(ROOT, "contest.json")   # 比赛主持方创建：{"start": epoch, "duration_min": 30, "cooldown_s": 15}
RUN_LOG = os.path.join(ROOT, "runs.jsonl")           # 每次自测追加一行，赛后画进化曲线


def _contest():
    try:
        with open(CONTEST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _last_run_time():
    try:
        with open(RUN_LOG, encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        return json.loads(lines[-1])["wall"] if lines else None
    except (OSError, ValueError, KeyError):
        return None


def _wait_cooldown(contest):
    last = _last_run_time()
    gap = contest.get("cooldown_s", 0)
    if last is not None and time.time() - last < gap:
        wait = gap - (time.time() - last)
        print(f"⏳ 自测冷却中（两次自测至少间隔 {gap}s），自动等待 {wait:.1f}s ……")
        sys.stdout.flush()
        time.sleep(wait)


def _clock_line(contest):
    left = contest["start"] + contest["duration_min"] * 60 - time.time()
    if left <= 0:
        return "⏰ 比赛时间已到！你的 bot 文件已被冻结，后续修改不计入成绩。"
    return f"⏱️ 比赛剩余时间: {int(left // 60)} 分 {int(left % 60):02d} 秒"


def _log_run(path, mode, dist, error=None, track=physics.DEFAULT_TRACK):
    try:
        with open(path, "rb") as f:
            sha = hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        sha = None
    contest = _contest()
    rec = {"wall": time.time(), "elapsed_s": round(time.time() - contest["start"], 1) if contest else None,
           "bot": os.path.basename(path), "sha": sha, "mode": mode, "track": track, "dist": dist, "error": error}
    with open(RUN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="QWOP Arena 无头自测")
    ap.add_argument("bot", help="bot 文件路径，例如 bots/bot_red.py")
    ap.add_argument("--mode", choices=sorted(physics.MODES), default="normal")
    ap.add_argument("--track", choices=sorted(physics.TRACKS), default=physics.DEFAULT_TRACK, help="obstacle=障碍赛, flat=无障碍")
    ap.add_argument("--time", type=float, default=physics.SIM_TIME, help="模拟时长 (秒)")
    ap.add_argument("--getup", action="store_true", help="起身测试：从趴着/仰躺两个起点各测一次")
    args = ap.parse_args()

    contest = _contest()
    if contest:
        _wait_cooldown(contest)
        print(_clock_line(contest))
    t0 = time.perf_counter()
    try:
        act = load_bot(args.bot)
    except (BotError, SyntaxError) as e:
        print(f"❌ 载入 bot 失败：{e}")
        _log_run(args.bot, args.mode, None, str(e), args.track)
        sys.exit(2)
    if args.getup:
        total, text = getup_report(act, args.bot, args.mode)
        _log_run(args.bot, args.mode, round(total, 3), None, "getup")
        print(text)
        return
    result = simulate(act, args.mode, args.time, args.track)
    _log_run(args.bot, args.mode, round(result["score"], 3), "act 异常" if result["errors"] else None, args.track)
    print(report(result, args.bot, time.perf_counter() - t0))


if __name__ == "__main__":
    main()
