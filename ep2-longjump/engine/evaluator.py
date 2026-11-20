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
import types
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
    return down_reason(obs) is not None


def down_reason(obs):
    """is_down 的具体触发条件（报告用的文字）；没倒地返回 None。倒地的唯一定义在这里。"""
    if obs["contact"]["torso"]:
        return "躯干/头触地"
    if obs["y"] < Y_DOWN:
        return f"躯干中心高度 {obs['y']:.2f}m < {Y_DOWN}m"
    ang = (obs["torso_angle"] + math.pi) % (2 * math.pi) - math.pi
    if abs(ang) >= LEAN_DOWN and obs["y"] < Y_LEAN_DOWN:
        return f"躯干倾角 {math.degrees(ang):+.0f}° 且躯干中心高度 {obs['y']:.2f}m < {Y_LEAN_DOWN}m"
    return None


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
    sx = ns.get("START_X", 0.0)
    if physics.TRACKS['jump'].get('free_takeoff'):
        # v4：START_X 是初始最前脚尖坐标，助跑距离 = 踏板坐标 - START_X。
        try:
            sx = float(sx)
        except (TypeError, ValueError, OverflowError) as exc:
            raise BotError(_start_msg()) from exc
        hi = physics.TRACKS['jump'].get('max_start')
        if not math.isfinite(sx) or not 2.0 <= sx <= (physics.TRACKS['jump']['line'] if hi is None else hi):
            raise BotError(_start_msg())
    else:
        try:
            sx = max(-11.5, min(0.0, float(sx)))
        except (TypeError, ValueError):
            sx = 0.0
    act.start_x = sx
    return act


class _ReadOnlyModule(types.ModuleType):
    """bot 拿到的 math/random：真模块公开属性的只读副本。bot 与引擎同进程，若直接给真模块，
    bot 可以改写 math.isfinite / math.radians 等引擎在用的函数（physics.apply、踝联动），或往模块上挂状态。"""

    def __setattr__(self, k, v):
        raise AttributeError(f"禁止修改模块 {self.__name__} 的属性 {k}")

    def __delattr__(self, k):
        raise AttributeError(f"禁止修改模块 {self.__name__} 的属性 {k}")


def _safe_import(name, *args, **kwargs):
    if name.split(".")[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"禁止导入 {name}")
    real = __import__(name, *args, **kwargs)
    proxy = _ReadOnlyModule(real.__name__)
    proxy.__dict__.update({k: v for k, v in vars(real).items() if not k.startswith("_")})
    return proxy


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
            if len(action) not in (physics.N_ACTIONS, physics.N_ACTIONS + 2):
                raise ValueError(f"act 必须返回 {physics.N_ACTIONS} 个数（踝可控时 {physics.N_ACTIONS + 2} 个），实际返回 {len(action)} 个")
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
    return {"frames": frames, "final": final, "score": score, "lost": lost, "errors": errors, "finish_time": finish_time, "mode": mode, "track": track}


def _tag_obs(obs, attempt, context=None):
    """把轮转赛制的上下文塞进观测（自测时分别是 0 / 0 / 0）。"""
    context = MATCH_CTX if context is None else context
    obs["attempt"] = attempt
    for key in ("leader", "best_so_far", "rank", "qualification_line", "bronze_line"):
        obs[key] = context.get(key, 0)
    obs["round"] = context.get("round") or attempt
    return obs


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


# ------------------------------------------------------------ 跳远（第 2 期）----
JUMP_ATTEMPTS = 3           # 每次评测的试跳次数（v1 规则）；实际用 physics.TRACKS["jump"]["attempts"]（随 --jump-rules 切换）
JUMP_TIME = 20.0            # 每次试跳的时间上限 (s)
# 轮转赛制下由调度脚本写入：leader = 全场当前最好成绩，best_so_far = 本选手前几跳最好，rank = 当前名次
MATCH_CTX = {"leader": 0.0, "best_so_far": 0.0, "rank": 0, "round": 0}
# 自测用的"模拟赛况"（--scenario）：让选手能分别测试稳妥 / 冲击方案的切换逻辑。数值只是典型例子，正式赛按真实结算传入。
SCENARIOS = {
    "no_mark": dict(round=3, leader=3.0, best_so_far=0.0, rank=16, qualification_line=1.6, bronze_line=2.5,
                    desc="第 3 跳，前两跳都没有成绩（排在最后），前八线 1.6m"),
    "behind": dict(round=4, leader=3.3, best_so_far=1.9, rank=7, qualification_line=1.7, bronze_line=2.6,
                   desc="决赛第 4 跳，已进前八但第 7 名，落后铜牌线 2.6m"),
    "leading": dict(round=4, leader=3.3, best_so_far=3.3, rank=1, qualification_line=1.7, bronze_line=2.6,
                    desc="决赛第 4 跳，自己 3.3m 领先"),
}
def _start_msg():
    line = physics.TRACKS['jump']['line']
    hi = physics.TRACKS['jump'].get('max_start')
    hi = line if hi is None else hi
    return f'START_X 必须是2～{hi:g}之间的有限数字；2={line-2:g}米接近距离，{hi:g}={line-hi:g}米接近距离'


SETTLE_SPEED = 0.2          # 落地后躯干速度低于此值 (m/s) ……
SETTLE_HOLD = 1.0           # …… 并连续保持此时长 (s) 视为"落稳"
SAND_END_MAX = 4.0          # v4 触沙放松：进沙后躯干落稳（同上两条）即结束本跳，最晚进沙后这么多秒结束（转播不用干等到 20 秒）
JUMP_TAIL = 1.0             # 判定结束后再多模拟的时长 (s)，只用于回放，不影响成绩
# 门槛依据新身体校准（见 REPORT_JUMP.md）：搜索最好 2.48m（很脆），相位锁定稳健版 2.24m，立定跳 1.51m（起跳线紧贴脚尖），
# 简单手写双腿蹬地 0.37m；扑倒/翻滚/走路碰巧腾空一步 0.01~0.08m（D 门槛 0.3 把这些压在 E）
JUMP_GRADES = [(2.5, "S", "飞人"), (1.8, "A", "一跃成名"), (1.2, "B", "像模像样"),
               (0.6, "C", "蹦了一下"), (0.3, "D", "聊胜于无"), (-1e9, "E", "没有成绩")]


def jump_attempt(act, attempt=1, mode="normal", sim_time=JUMP_TIME, on_step=None,
                 wind_seed=None, wind_strength=1.0, match_ctx=None):
    """跑一次试跳，返回逐帧记录与判定结果。规则见 PROMPT_ep2.md / JUMP_DESIGN.md。

    状态机（每个控制帧 step 之后，用该帧 4 个物理子步里出现过的全部地面接触点判定，见 physics.SUBSTEP_CONTACTS）：
      RUNUP  助跑：有任何接触点 x > 起跳线 -> 犯规；没有任何身体部位触地 -> FLIGHT（记录起跳）
      FLIGHT 腾空：重新触地时，若所有接触点都在线后(<=线) -> 回到 RUNUP（只是跑步的一步）；
             否则 -> LANDED（这次腾空就是起跳，首次触地 = 落地）
      LANDED 落地后：持续记录接触点最小 x（"最靠后的沙坑印记"）；
             倒地（is_down，与计分/报告同一定义），或躯干速度 < SETTLE_SPEED 连续 SETTLE_HOLD 秒，或时间到 -> 落稳，结算：
             成绩 = max(0, min(落稳时刻全身最靠后的 x, 落地以来最靠后的接触点 x) − 起跳线)
    """
    if not math.isfinite(sim_time) or sim_time <= 0 or round(sim_time * physics.FPS) < 1:
        raise ValueError('试跳时长必须至少包含一帧且为有限正数')
    random.seed(20260101 + attempt)
    line = physics.TRACKS["jump"]["line"]
    if physics.TRACKS["jump"].get("free_takeoff"):
        seed = physics.WIND_DEFAULT_SEED + attempt if wind_seed is None else wind_seed
        return jump_attempt_free(act, attempt, mode, sim_time, on_step, seed, wind_strength, match_ctx)
    runner = physics.Runner(mode, "jump")
    frames, errors = [], []
    last_action = list(physics.REST_ACTION)
    state = "RUNUP"
    res = {"attempt": attempt, "state": None, "foul": None, "landed": False, "score": 0.0,
           "takeoff_t": None, "takeoff_x": None, "takeoff_vx": None, "takeoff_vy": None, "line_vx": None, "line_t": None,
           "land_t": None, "land_parts": None, "land_ang": None, "flight_time": None, "peak_y": None,
           "mark_x": None, "mark_part": None, "rear_x": None, "rear_part": None, "settle_t": None,
           "settle_ang": None, "settle_y": None, "settle_reason": None, "ref_x": None, "ref_part": None, "ref_src": None,
           "judge_k": None, "runup_hops": 0, "errors": errors}
    last_contact_xmax = None
    slow, end_k = 0, None
    n = int(round(sim_time * physics.FPS))
    k = 0
    peak = 0.0
    while k < (end_k if end_k is not None else n):
        obs = runner.observe()
        _tag_obs(obs, attempt, match_ctx)
        try:
            action = act(_copy_obs(obs))
            action = [float(v) for v in action]
            if len(action) == 4:
                action += physics.REST_ACTION[4:]
            if len(action) not in (physics.N_ACTIONS, physics.N_ACTIONS + 2):
                raise ValueError(f"act 必须返回 {physics.N_ACTIONS} 个数（踝可控时 {physics.N_ACTIONS + 2} 个），实际返回 {len(action)} 个")
            last_action = action
        except Exception:  # noqa: BLE001
            if len(errors) < 3:
                errors.append((obs["time"], traceback.format_exc(limit=3)))
            elif len(errors) == 3:
                errors.append((obs["time"], "……后续错误已省略"))
            action = last_action
        runner.step(action)
        if on_step is not None:
            on_step(runner, obs)
        obs["action"] = [max(-1.0, min(1.0, a)) for a in action]
        obs["jstate"] = state
        frames.append(obs)
        k += 1
        if end_k is not None:
            continue
        t = runner.time
        tx = runner.torso.position.x / physics.PPM
        if res["line_vx"] is None and tx >= line:
            res["line_vx"], res["line_t"] = runner.torso.velocity.x / physics.PPM, t
        cs = runner.step_contacts if physics.SUBSTEP_CONTACTS else runner.ground_contacts()
        if state == "RUNUP":
            beyond = [(sh, x) for sh, x in cs if x > line]
            if beyond:
                sh, x = max(beyond, key=lambda p: p[1])
                res["foul"] = {"t": t, "part": runner.shape_names[sh], "x": x}
                state = "FOUL"
                res["judge_k"] = k
                end_k = k + int(JUMP_TAIL * physics.FPS)
                continue
            if cs:
                last_contact_xmax = max(x for _s, x in cs)
            else:
                state = "FLIGHT"
                res["takeoff_t"], res["takeoff_x"] = t, last_contact_xmax
                res["takeoff_vx"] = runner.torso.velocity.x / physics.PPM
                res["takeoff_vy"] = runner.torso.velocity.y / physics.PPM
                peak = runner.torso.position.y / physics.PPM
        elif state == "FLIGHT":
            peak = max(peak, runner.torso.position.y / physics.PPM)
            if cs:
                if all(x <= line for _s, x in cs):
                    state = "RUNUP"
                    res["runup_hops"] += 1
                    last_contact_xmax = max(x for _s, x in cs)
                else:
                    state = "LANDED"
                    res["landed"] = True
                    res["land_t"] = t
                    res["flight_time"] = t - res["takeoff_t"]
                    res["peak_y"] = peak
                    res["land_parts"] = sorted({runner.shape_names[sh] for sh, _x in cs})
                    res["land_ang"] = -runner.torso.angle
                    sh, x = min(cs, key=lambda p: p[1])
                    res["mark_x"], res["mark_part"] = x, runner.shape_names[sh]
        if state == "LANDED":
            for sh, x in cs:
                if x < res["mark_x"]:
                    res["mark_x"], res["mark_part"] = x, runner.shape_names[sh]
            v = runner.torso.velocity.length / physics.PPM
            slow = slow + 1 if v < SETTLE_SPEED else 0
            dr = down_reason(runner.observe())
            reason = f"倒地：{dr}" if dr else (f"躯干速度 < {SETTLE_SPEED} m/s 持续 {SETTLE_HOLD:g} 秒" if slow >= int(SETTLE_HOLD * physics.FPS) else None)
            if reason is None and k >= n:
                reason = "时间到"
            if reason:
                _settle(res, runner, t, reason, line)
                state = "DONE"
                res["judge_k"] = k
                end_k = k + int(JUMP_TAIL * physics.FPS)
    res["state"] = state
    res["frames"] = frames
    res["final"] = runner.observe()
    return res


def _settle(res, runner, t, reason, line):
    rx, rp = runner.min_x()
    res["settle_t"], res["settle_reason"] = t, reason
    res["rear_x"], res["rear_part"] = rx, rp
    res["settle_ang"] = -runner.torso.angle
    res["settle_y"] = runner.torso.position.y / physics.PPM
    if rx <= res["mark_x"]:
        ref, part, src = rx, rp, "落稳时全身最靠后"
    else:
        ref, part, src = res["mark_x"], res["mark_part"], "沙坑最靠后的触地印记"
    res["ref_x"], res["ref_part"], res["ref_src"] = ref, part, src
    res["score"] = max(0.0, ref - line)


def jump_attempt_free(act, attempt=1, mode="normal", sim_time=JUMP_TIME, on_step=None,
                      wind_seed=None, wind_strength=1.0, match_ctx=None):
    """v4：板前自由选起跳位置，固定起跳线计量，必须实际落地进坑。

    选取第一次落进沙坑的腾空，检查最后触地点是否越板。
    落点只取落地以来最靠后的真实触地印记，悬空部位不参与计量。
    """
    random.seed(20260101 + attempt)
    start_x = getattr(act, "start_x", 0.0) or 0.0
    runner = physics.Runner(mode, "jump", start_x=start_x, wind_seed=wind_seed, wind_strength=wind_strength)
    frames, conts, errors = [], [], []
    last_action = list(physics.REST_ACTION)
    res = {"attempt": attempt, "state": None, "foul": None, "landed": False, "score": 0.0, "free": True,
           "start_x": start_x, "wind_seed": wind_seed, "wind_strength": wind_strength,
           "match_ctx": dict(MATCH_CTX if match_ctx is None else match_ctx),
           "takeoff_t": None, "takeoff_x": None, "takeoff_vx": None, "takeoff_vy": None, "line_vx": None, "line_t": None,
           "land_t": None, "land_parts": None, "land_ang": None, "flight_time": None, "peak_y": None,
           "mark_x": None, "mark_part": None, "rear_x": None, "rear_part": None, "settle_t": None,
           "settle_ang": None, "settle_y": None, "settle_reason": None, "ref_x": None, "ref_part": None,
           "ref_src": None, "judge_k": None, "runup_hops": 0, "errors": errors}
    n = int(round(sim_time * physics.FPS))
    sand_k, slow = None, 0
    for k in range(n):
        if getattr(runner, "sand_torque", None) is not None and runner.in_sand:
            sand_k = k if sand_k is None else sand_k
            v = math.hypot(runner.torso.velocity.x, runner.torso.velocity.y) / physics.PPM
            slow = slow + 1 if v < SETTLE_SPEED else 0
            if slow >= int(SETTLE_HOLD * physics.FPS) or k - sand_k >= int(SAND_END_MAX * physics.FPS):
                n = k
                res["sand_end"] = "落稳" if slow >= int(SETTLE_HOLD * physics.FPS) else f"进沙 {SAND_END_MAX:g} 秒"
                break
        obs = runner.observe()
        _tag_obs(obs, attempt, match_ctx)
        try:
            action = act(_copy_obs(obs))
            action = [float(v) for v in action]
            if len(action) == 4:
                action += physics.REST_ACTION[4:]
            if len(action) not in (physics.N_ACTIONS, physics.N_ACTIONS + 2):
                raise ValueError(f"act 必须返回 {physics.N_ACTIONS} 个数（踝可控时 {physics.N_ACTIONS + 2} 个），实际返回 {len(action)} 个")
            last_action = action
        except Exception:  # noqa: BLE001
            if len(errors) < 3:
                errors.append((obs["time"], traceback.format_exc(limit=3)))
            elif len(errors) == 3:
                errors.append((obs["time"], "……后续错误已省略"))
            action = last_action
        runner.step(action)
        cs = runner.step_contacts if physics.SUBSTEP_CONTACTS else runner.ground_contacts()
        conts.append(list(cs))
        if on_step is not None:
            on_step(runner, obs)
        obs["action"] = [max(-1.0, min(1.0, a)) for a in action]
        obs["jstate"] = "FREE"
        frames.append(obs)

    # ---- 后处理：找最长的一段腾空 = 这次起跳 ----
    flights, k = [], 0
    while k < n:
        if not conts[k]:
            s = k
            while k < n and not conts[k]:
                k += 1
            flights.append((s, k))
        else:
            k += 1
    if not flights:
        res["state"] = "NO_JUMP"
        res["judge_k"] = n - 1
    else:
        pick = physics.TRACKS["jump"].get("free_pick", "pit")
        pit_start = physics.TRACKS["jump"]["pit"][0]
        if pick == "last":
            # 取"最后一次起跳"（最后一个持续 ≥0.10s 的腾空段）
            real = [f for f in flights if (f[1] - f[0]) >= int(0.10 * physics.FPS)] or flights
            sel = real[-1]
        elif pick == "longest":
            sel = max(flights, key=lambda se: se[1] - se[0])
        else:
            # 默认：取"落进沙坑的那一次腾空"（第一次落地的最靠后触点在沙坑里）
            cand = []
            for s0, e0 in flights:
                if e0 < n and conts[e0] and min(x for _s, x in conts[e0]) >= pit_start - 1e-9:
                    cand.append((s0, e0))
            sel = cand[0] if cand else None
        if sel is None:
            res["state"] = "SHORT"
            res["in_pit"] = False
            res["judge_k"] = n - 1
            res["settle_t"] = frames[-1]["time"]
            res["rear_x"], res["rear_part"] = runner.min_x()
            res["frames"] = frames
            res["final"] = runner.observe()
            return res
        s, e = sel
        res["runup_hops"] = len(flights) - 1
        res["takeoff_t"] = frames[s - 1]["time"] if s > 0 else 0.0
        if s > 0 and conts[s - 1]:
            sh, tx = max(conts[s - 1], key=lambda p: p[1])
            res["takeoff_x"], res["takeoff_part"] = tx, runner.shape_names[sh]
        else:
            res["takeoff_x"] = frames[s]["x"]
        res["takeoff_vx"] = frames[s - 1]["vx"] if s > 0 else frames[s]["vx"]
        res["takeoff_vy"] = frames[s - 1]["vy"] if s > 0 else frames[s]["vy"]
        board = physics.TRACKS["jump"]["line"]        # 起跳板（沙坑边缘）
        if res["takeoff_x"] is not None and res["takeoff_x"] > board + 1e-9:
            # 越过起跳板才起跳（在沙坑里起跳）= 犯规
            res["foul"] = {"t": res["takeoff_t"], "part": res.get("takeoff_part"),
                           "x": res["takeoff_x"] - board}
            res["state"] = "FOUL"
            res["in_pit"] = None
            res["judge_k"] = n - 1
            res["settle_t"] = frames[-1]["time"]
            res["rear_x"], res["rear_part"] = runner.min_x()
            res["frames"] = frames
            res["final"] = runner.observe()
            return res
        res["flight_time"] = (e - s) / physics.FPS
        res["peak_y"] = max(f["y"] for f in frames[s:min(e + 1, n)])
        res["landed"] = e < n and bool(conts[e])
        if res["landed"]:
            res["land_t"] = frames[e]["time"]
            res["land_parts"] = sorted({runner.shape_names[sh] for sh, _x in conts[e]})
            res["land_ang"] = -runner.torso.angle
            mark, part = None, None
            for kk in range(e, n):
                for sh, x in conts[kk]:
                    if mark is None or x < mark:
                        mark, part = x, runner.shape_names[sh]
            res["mark_x"], res["mark_part"] = mark, part
        res["settle_t"] = frames[-1]["time"]
        res["settle_reason"] = res.get("sand_end") or "时间到"
        res["settle_ang"] = -runner.torso.angle
        res["settle_y"] = runner.torso.position.y / physics.PPM
        rx, rp = runner.min_x()
        res["rear_x"], res["rear_part"] = rx, rp
        res["ref_x"], res["ref_part"] = res["mark_x"], res["mark_part"]
        res["ref_src"] = "落地以来最靠后的真实触地印记"
        pit_start = physics.TRACKS["jump"]["pit"][0]
        _measure_board_jump(res, board, pit_start)
        res["judge_k"] = n - 1
        res["state"] = ("DONE" if res["landed"] else "NO_LANDING") if res["in_pit"] else "SHORT"
    res["frames"] = frames
    res["final"] = runner.observe()
    return res


def _measure_board_jump(res, board, pit_start):
    res["in_pit"] = res["ref_x"] is not None and res["ref_x"] >= pit_start - 1e-9
    valid = res["landed"] and not res["foul"] and res["in_pit"]
    res["score"] = max(0.0, res["ref_x"] - board) if valid else 0.0
    res["board_x"] = board
    res["takeoff_margin"] = board - res["takeoff_x"]


def _shared_objects():
    """bot 能拿到的、能挂属性的进程内共享对象：math/random 模块、random 里的类，以及这些模块/类里的
    Python 函数（如 random.main、random.Random.seed —— 函数对象可以挂属性）。往上面挂属性能让状态跨试跳保留。
    （bot 拿到的模块本身是只读副本，见 _ReadOnlyModule；模块仍列在这里以防万一。）"""
    objs, seen, todo = [], set(), [math, random]
    while todo:
        o = todo.pop()
        if id(o) in seen:
            continue
        seen.add(id(o))
        objs.append(o)
        for v in list(vars(o).values()):
            if isinstance(v, (classmethod, staticmethod)):
                v = v.__func__
            if isinstance(v, types.FunctionType) or (isinstance(v, type) and v.__module__ == "random"):
                todo.append(v)
    return objs


def _snapshot_shared():
    return [(o, dict(vars(o))) for o in _shared_objects()]


def _restore_shared(snap):
    """把共享对象恢复到快照状态；返回被改动的属性名列表（random 内部随机数状态不算）。"""
    changed = []
    for o, d in snap:
        cur = vars(o)
        for k in [k for k in cur if k not in d]:
            changed.append(f"{getattr(o, '__qualname__', getattr(o, '__name__', o))}.{k}")
            delattr(o, k)
        for k, v in d.items():
            if k in cur and cur[k] is not v:
                changed.append(f"{getattr(o, '__qualname__', getattr(o, '__name__', o))}.{k}")
                setattr(o, k, v)
    return changed


def jump_eval(make_act, mode="normal", attempts=None, sim_time=JUMP_TIME, on_step=None,
              wind_seed=physics.WIND_DEFAULT_SEED, wind_strength=1.0, scenario=None):
    """make_act(): 每次试跳前调用，返回全新的 act（文件重新载入，模块级全局变量全部重置）。
    每跳之后把 math/random 模块（及 random 里的类）上被 bot 新增/改动的属性复原，防止状态跨试跳保留。"""
    attempts = attempts or physics.TRACKS["jump"]["attempts"]
    ctx = {k: v for k, v in SCENARIOS[scenario].items() if k != "desc"} if scenario else {}
    outs = []
    for a in range(1, attempts + 1):
        cb = (lambda r, o, a=a: on_step(a, r, o)) if on_step else None
        snap = _snapshot_shared()
        random.seed(20260101 + a)
        try:
            # 模拟赛况时每跳都按该轮次传 attempt/round（风仍按第 a 跳变化）
            res = jump_attempt(make_act(), ctx.get("round", a), mode, sim_time, cb,
                               wind_seed=wind_seed + a, wind_strength=wind_strength, match_ctx=ctx)
        finally:
            changed = _restore_shared(snap)
        if scenario:
            res["attempt"] = a          # 报告里按第几次自测编号；bot 看到的 attempt/round 是模拟轮次
        res["leak"] = changed
        outs.append(res)
    best = max(outs, key=lambda r: r["score"])
    return {"attempts": outs, "best": best, "score": best["score"], "mode": mode, "scenario": scenario}


def _ang(a):
    return f"{a * 57.3:+.0f}°"


def jump_report(ev, path, wall):
    line = physics.TRACKS["jump"]["line"]
    free = bool(physics.TRACKS["jump"].get("free_takeoff"))
    best = ev["best"]
    head = (f"板前自选起跳位置（成绩从 x={line:g}m 起跳线量）" if free else f"助跑道 {line:g}m + 沙坑")
    L = [f"================ 🦘 跳远自测报告 ({os.path.basename(path)}, {head}, 模式: {ev['mode']}, "
         f"{len(ev['attempts'])} 次试跳, 耗时 {wall:.2f}秒) ================"]
    # 第 2 期（2026-11-04 主持定）：评级门槛赛后再定，自测报告不显示评级，只报成绩
    if ev.get("scenario"):
        c = SCENARIOS[ev["scenario"]]
        L.append(f"[模拟赛况 {ev['scenario']}] {c['desc']}：obs 里 attempt=round={c['round']}、leader={c['leader']}、best_so_far={c['best_so_far']}、"
                 f"rank={c['rank']}、qualification_line={c['qualification_line']}、bronze_line={c['bronze_line']}（5 跳只有风不同）")
    L.append(f"[最终成绩] 最好一跳: {ev['score']:.2f} 米 (第 {best['attempt']} 跳)")
    L.append(f"[{len(ev['attempts'])} 次试跳] " + " | ".join(f"第{r['attempt']}跳 " + _short(r) for r in ev["attempts"]))
    for r in ev["attempts"]:
        L.append("")
        L.append(f"--- 第 {r['attempt']} 跳：{_short(r)} ---")
        frames = r["frames"]
        jf = _judged(r)
        if r.get("free"):
            maxx = max((f["x"] for f in jf), default=0.0)
            sx = r.get('start_x', 0.0)
            L.append(f"助跑: 起始脚尖 x={sx:.2f}m，可用助跑距离 {line-sx:.2f}m，躯干最远跑到 x={maxx:.2f}m"
                     + (f"，全程 {r['runup_hops']} 次跑步腾空" if r["runup_hops"] else ""))
            if r["foul"]:
                fo = r["foul"]
                L.append(f"🚩 犯规: {fo['t']:.2f}s 起跳时脚尖在起跳板（{line:g}m）后面 {fo['x']:.2f}m ——"
                         f"必须在板前起跳，越过板再起跳算犯规 → 本跳 0 分")
            elif r["landed"]:
                L.append(f"起跳: {r['takeoff_t']:.2f}s，起跳点（脚尖）x={r['takeoff_x']:.2f}m，"
                         f"起跳瞬间躯干速度 水平 {r['takeoff_vx']:.2f} / 竖直 {r['takeoff_vy']:+.2f} m/s")
                L.append(f"腾空: {r['flight_time']:.2f}s（这一次起跳），躯干中心最高 {r['peak_y']:.2f}m")
                L.append(f"落地: {r['land_t']:.2f}s，首先触地的部位 {'、'.join(r['land_parts'])}，躯干倾角 {_ang(r['land_ang'])}")
                L.append(f"落点: 全身最靠后 x={r['rear_x']:.2f}m（{r['rear_part']}），"
                         f"落地以来最靠后的触地印记 x={r['mark_x']:.2f}m（{r['mark_part']}）" if r["mark_x"] is not None else "")
                L.append(f"计量: 取{r['ref_src']}（{r['ref_part']}）x={r['ref_x']:.2f} − 起跳线 x={line:.2f} → 本跳成绩 {r['score']:.2f}m")
                if r.get("in_pit") is False:
                    pit0 = physics.TRACKS["jump"]["pit"][0]
                    L.append(f"❌ 但落点 x={r['ref_x']:.2f}m 没进沙坑（沙坑从 {pit0:g}m 开始）→ 本跳 0 分")
            else:
                L.append(f"未进沙坑: 20 秒内没有任何一次腾空的落点在沙坑里（{line:g}m 之后）→ 本跳 0 分")
            segs = segments(frames, classify(frames))
            L.append("姿态（含判定后 1 秒回放）: " + "；".join(f"{sg['start']}~{sg['end']}s {LABELS[sg['lab']][0]}" for sg in segs))
            if r["errors"]:
                L.append("🐞 act() 报错：")
                for t, tb in r["errors"]:
                    L.append(f"t={t:.2f}s:\n{tb.rstrip()}")
            continue
        near = [f["vx"] for f in jf if 0 < f["dist_to_line"] <= 2.0]
        L.append("助跑: 躯干到达起跳线 " + (f"{r['line_t']:.2f}s，此时水平速度 {r['line_vx']:.2f} m/s" if r["line_t"] is not None else "未到达（判定结束前）")
                 + (f" | 起跳线前最后 2m 躯干平均水平速度 {sum(near) / len(near):.2f} m/s" if near else ""))
        if r["foul"]:
            fo = r["foul"]
            L.append(f"🚩 犯规: {fo['t']:.2f}s 时【{fo['part']}】在起跳线前方（沙坑一侧）{fo['x'] - line:+.2f}m 处触地，触地前没有从线后腾空"
                     + (f"（此前助跑中有 {r['runup_hops']} 次腾空，都落回了线后）" if r["runup_hops"] else "") + " → 本跳 0 分")
        elif r["landed"]:
            L.append(f"起跳: {r['takeoff_t']:.2f}s，最后触地点在起跳线{'前' if r['takeoff_x'] > line else '后'} {abs(line - r['takeoff_x']):.2f}m，"
                     f"起跳瞬间躯干速度 水平 {r['takeoff_vx']:.2f} / 竖直 {r['takeoff_vy']:+.2f} m/s")
            L.append(f"腾空: {r['flight_time']:.2f}s，躯干中心最高 {r['peak_y']:.2f}m")
            L.append(f"落地: {r['land_t']:.2f}s，首先触地的部位 {'、'.join(r['land_parts'])}，躯干倾角 {_ang(r['land_ang'])}")
            L.append(f"落稳: {r['settle_t']:.2f}s（{r['settle_reason']}），躯干倾角 {_ang(r['settle_ang'])}、躯干中心高度 {r['settle_y']:.2f}m；"
                     f"全身最靠后 {r['rear_x'] - line:+.2f}m（{r['rear_part']}），落地以来最靠后的触地印记 {r['mark_x'] - line:+.2f}m（{r['mark_part']}）")
            L.append(f"计量: 取{r['ref_src']}（{r['ref_part']}）距起跳线 {r['ref_x'] - line:+.2f}m → 本跳成绩 {r['score']:.2f}m"
                     + ("（身体没有完全越过起跳线，记 0）" if r["ref_x"] <= line else ""))
        else:
            air = "（时间到时身体仍在空中）" if r["state"] == "FLIGHT" else ""
            hops = (f"；助跑中有 {r['runup_hops']} 次腾空，每次都落回起跳线后（算助跑的一步）" if r["runup_hops"]
                    else "；全程没有出现过全身离地")
            L.append(f"未起跳: {len(frames) / physics.FPS:.0f}s 内没有从线后腾空并落入沙坑{air}{hops} → 本跳 0 分（躯干最终位置 x={r['final']['x']:.2f}m）")
        segs = segments(frames, classify(frames))
        L.append("姿态（含判定后 1 秒回放）: " + "；".join(f"{sg['start']}~{sg['end']}s {LABELS[sg['lab']][0]}" for sg in segs))
        if r["errors"]:
            L.append("🐞 act() 报错：")
            for t, tb in r["errors"]:
                L.append(f"t={t:.2f}s:\n{tb.rstrip()}")
    L.append("")
    L.append("[📌 关键事件]")
    i = 0
    for r in ev["attempts"]:
        for h in _jump_facts(r, line, free):
            i += 1
            L.append(f"{i}. 第 {r['attempt']} 跳：{h}")
    if len(ev["attempts"]) > 1 and all(_sig(r) == _sig(ev["attempts"][0]) for r in ev["attempts"]):
        i += 1
        L.append(f"{i}. {len(ev['attempts'])} 次试跳的结果完全相同。")
    L.append("=" * 72)
    return "\n".join(L)


def _judged(r):
    """判定结束（犯规/落稳/时间到）之前的帧；之后多模拟的 JUMP_TAIL 秒只用于回放。"""
    return r["frames"][:r["judge_k"]] if r["judge_k"] else r["frames"]


def _sig(r):
    return (round(r["score"], 4), r["state"], r["foul"] and round(r["foul"]["t"], 3), r["takeoff_t"])


def _short(r):
    if r["foul"]:
        return "🚩犯规 0.00m"
    if r.get("free") and r.get("in_pit") is False:
        return "未进沙坑 0.00m"
    if not r["landed"]:
        return "未起跳 0.00m"
    return f"{r['score']:.2f}m"


def _jump_facts(r, line, free=False):
    if free:
        out = []
        frames = _judged(r)
        if r["foul"]:
            out.append(f"犯规：起跳时脚尖已经越过起跳板 {r['foul']['x']:.2f}m，本跳 0 分。")
        elif not r["landed"]:
            out.append("没有任何一次腾空落进沙坑，本跳 0 分。")
        else:
            out.append(f"起跳点 x={r['takeoff_x']:.2f}m，腾空 {r['flight_time']:.2f}s，落点 x={r['ref_x']:.2f}m → 成绩 {r['score']:.2f}m。")
            if r.get("in_pit") is False:
                out.append(f"落点没进沙坑（{physics.TRACKS['jump']['pit'][0]:g}m 之后才算），本跳 0 分。")
            if r["flight_time"] < 0.15:
                out.append(f"这段腾空只有 {r['flight_time']:.2f}s，可能是跑步步伐而不是起跳。")
            if r["ref_x"] < r["takeoff_x"]:
                out.append("落点比起跳点还靠后（往后摔了），本跳记 0。")
        if r.get("leak"):
            out.append(f"bot 在共享对象上改动了属性 {', '.join(r['leak'][:5])}，本跳结束后已复原。")
        if r["errors"]:
            out.insert(0, "act() 抛出了异常（出错帧沿用上一帧动作）。")
        return out
    out = []
    frames = _judged(r)
    first_down = next((f for f in frames if is_down(f)), None)
    if first_down and first_down["dist_to_line"] > 0 and (r["takeoff_t"] is None or not r["landed"] or first_down["time"] < r["takeoff_t"]):
        out.append(f"t={first_down['time']:.1f}s 躯干还在助跑道上（距起跳线 {first_down['dist_to_line']:.2f}m）时倒地：{down_reason(first_down)}。")
    if r["line_t"] is None and not r["landed"]:
        out.append(f"躯干没有到达起跳线（躯干最远 x={max(f['x'] for f in frames):.2f}m，起跳线在 {line:g}m）。")
    if r["landed"] and r["flight_time"] < 0.15:
        out.append(f"起跳后的腾空只有 {r['flight_time']:.2f}s。")
    if r["landed"] and line - r["takeoff_x"] > 0.5:
        out.append(f"起跳点离起跳线 {line - r['takeoff_x']:.2f}m。")
    if r["landed"] and r["ref_src"] == "落稳时全身最靠后" and r["mark_x"] - r["rear_x"] > 0.01:
        out.append(f"落稳时【{r['rear_part']}】在最靠后的触地印记后方 {r['mark_x'] - r['rear_x']:.2f}m。")
    if r["landed"] and r["mark_part"] not in r["land_parts"]:
        out.append(f"落地后【{r['mark_part']}】在更靠后的位置触地，印记退到 {r['mark_x'] - line:+.2f}m。")
    if r.get("leak"):
        out.append(f"bot 在共享对象（math/random 模块、类或函数）上改动了属性 {', '.join(r['leak'][:5])}，本跳结束后已复原（状态不能跨试跳保留）。")
    if r["errors"]:
        out.insert(0, "act() 抛出了异常（出错帧沿用上一帧动作）。")
    if not out:
        out.append("没有触发任何关键事件。")
    return out


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


def _jump_run_extra(ev, args):
    """自测记录里自动附上起点和每跳要点（第 2 期起：不依赖选手日志也能看出打法变化）。"""
    r2 = lambda v: None if v is None else round(v, 2)
    rows = [{"a": r.get("attempt"), "score": r2(r.get("score")), "foul": bool(r.get("foul")),
             "takeoff_x": r2(r.get("takeoff_x")), "vx": r2(r.get("takeoff_vx")), "vy": r2(r.get("takeoff_vy")),
             "flight": r2(r.get("flight_time")), "hops": r.get("runup_hops")} for r in ev["attempts"]]
    sx = next((r.get("start_x") for r in ev["attempts"] if r.get("start_x") is not None), None)
    return {"start_x": sx, "wind_seed": args.wind_seed, "wind_strength": args.wind_strength,
            "scenario": getattr(args, "scenario", None), "attempts": rows}


def _log_run(path, mode, dist, error=None, track=physics.DEFAULT_TRACK, extra=None):
    try:
        with open(path, "rb") as f:
            sha = hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        sha = None
    contest = _contest()
    rec = {"wall": time.time(), "elapsed_s": round(time.time() - contest["start"], 1) if contest else None,
           "bot": os.path.basename(path), "sha": sha, "mode": mode, "track": track, "dist": dist, "error": error}
    if extra:
        rec.update(extra)
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
    ap.add_argument("--track", choices=sorted(physics.TRACKS), default=physics.DEFAULT_TRACK, help="obstacle=障碍赛, flat=无障碍, jump=跳远")
    ap.add_argument("--time", type=float, default=physics.SIM_TIME, help="模拟时长 (秒)")
    ap.add_argument("--getup", action="store_true", help="起身测试：从趴着/仰躺两个起点各测一次")
    ap.add_argument("--jump-rules", choices=sorted(physics.JUMP_RULES), default=physics.JUMP_DEFAULT_RULES,
                    help="跳远规则：v1=起跳线 9m 3 跳，v2=起跳线 3m 5 跳，v3=起跳线 5m 5 跳，v4=起跳线 12m（默认，单人练习 5 跳）")
    ap.add_argument("--wind-seed", type=int, default=physics.WIND_DEFAULT_SEED, help="练习风种子基数；第 k 跳使用基数+k")
    ap.add_argument("--wind-strength", type=float, default=1.0, help="风力倍率，0 为无风基线；只在 v4 生效")
    ap.add_argument("--attempts", type=int, choices=range(1, 6), help="单人自测次数；正式赛使用 jump_competition.py 的 3+2 轮转")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), help="模拟赛况（只用于自测切换逻辑）：no_mark / behind / leading；不写 = 普通自测（赛况全为 0）")
    ap.add_argument("--jump-body", choices=list(physics.JUMP_BODIES), default=physics.JUMP_DEFAULT_BODY,
                    help="跳远身体改动（主办方测试用；默认见 physics.JUMP_DEFAULT_BODY，回放同）")
    args = ap.parse_args()
    physics.configure_jump(args.jump_rules, args.jump_body)

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
    if args.track == "jump":
        ev = jump_eval(lambda: load_bot(args.bot), args.mode, attempts=args.attempts,
                       wind_seed=args.wind_seed, wind_strength=args.wind_strength, scenario=args.scenario)
        _log_run(args.bot, args.mode, round(ev["score"], 3), "act 异常" if any(r["errors"] for r in ev["attempts"]) else None, "jump",
                 _jump_run_extra(ev, args))
        print(jump_report(ev, args.bot, time.perf_counter() - t0))
        return
    result = simulate(act, args.mode, args.time, args.track)
    _log_run(args.bot, args.mode, round(result["score"], 3), "act 异常" if result["errors"] else None, args.track)
    print(report(result, args.bot, time.perf_counter() - t0))


if __name__ == "__main__":
    main()
