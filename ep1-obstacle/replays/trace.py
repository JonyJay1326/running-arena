"""把一场比赛导出成轻量回放 trace（JSON），供 replays/viewer.html 播放。

用法：
    python replays/trace.py bots/Astra/bot.py                 # 单份
    python replays/trace.py --all                              # bots/ 下全部
    python replays/trace.py bots/Astra/bot.py --out x.json --fps 30

不依赖第三方库，只用官方引擎；导出的是真实逐帧刚体姿态（不是重放动作）。
"""
import argparse
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(EP, "engine"))

import physics  # noqa: E402
import evaluator  # noqa: E402

PPM = physics.PPM

# 每个刚体的尺寸（m），viewer.html 里同名常量必须一致
SPEC = {
    "torso": (physics.TORSO_W / PPM, physics.TORSO_H / PPM),
    "head_r": physics.HEAD_R / PPM,
    "thigh": (physics.THIGH_W / PPM, physics.THIGH_H / PPM),
    "shin": (physics.SHIN_W / PPM, physics.SHIN_H / PPM),
    "foot": (physics.FOOT_L / PPM, physics.FOOT_H / PPM),
    "arm": (physics.ARM_W / PPM, physics.ARM_H / PPM),
    "fore": (physics.FORE_W / PPM, physics.FORE_H / PPM),
}


def _xyz(body):
    p = body.position
    return [round(p.x / PPM, 3), round(p.y / PPM, 3), round(body.angle, 3)]


def snapshot(runner, obs):
    head = runner.torso.local_to_world((0, physics.TORSO_H / 2 + physics.HEAD_R))
    row = [round(obs["time"], 3), round(obs["score"], 3)]
    row += _xyz(runner.torso)
    row += [round(head.x / PPM, 3), round(head.y / PPM, 3)]
    for i in (0, 1):
        for group in (runner.thighs, runner.shins, runner.foot_bodies,
                      runner.arms, runner.forearms):
            row += _xyz(group[i])
    return row


def run(bot_path, fps=30):
    act = evaluator.load_bot(bot_path)
    every = max(1, round(physics.FPS / fps))
    rows = []
    n = 0

    def on_step(runner, obs):
        nonlocal n
        if n % every == 0:
            rows.append(snapshot(runner, obs))
        n += 1

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):          # 吞掉选手代码里的 print
        res = evaluator.simulate(act, on_step=on_step)
    # 结尾补一帧，冲线/倒地后仍然看得到最后姿态
    rows.append(rows[-1])
    return {
        "fps": fps,
        "track": res["track"],
        "score_m": round(res["score"], 3),
        "finish_s": res["finish_time"],
        "frames": rows,
        "layout": ["t", "score", "torso.x", "torso.y", "torso.a", "head.x", "head.y"]
                  + ["L.thigh.x", "L.thigh.y", "L.thigh.a", "L.shin.x", "L.shin.y", "L.shin.a",
                     "L.foot.x", "L.foot.y", "L.foot.a", "L.arm.x", "L.arm.y", "L.arm.a",
                     "L.fore.x", "L.fore.y", "L.fore.a"]
                  + ["R.thigh.x", "R.thigh.y", "R.thigh.a", "R.shin.x", "R.shin.y", "R.shin.a",
                     "R.foot.x", "R.foot.y", "R.foot.a", "R.arm.x", "R.arm.y", "R.arm.a",
                     "R.fore.x", "R.fore.y", "R.fore.a"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bot", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()

    if a.all:
        root = os.path.join(EP, "bots")
        index = []
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name, "bot.py")
            if not os.path.exists(p):
                continue
            trace = run(p, a.fps)
            trace["name"] = name
            out = os.path.join(HERE, name + ".json")
            with open(out, "w", encoding="utf-8", newline="\n") as f:
                json.dump(trace, f, separators=(",", ":"))
            index.append({"name": name, "file": name + ".json",
                          "score_m": trace["score_m"], "finish_s": trace["finish_s"]})
            print(f"{name:<16} {trace['score_m']:>7.3f} m  {os.path.getsize(out)//1024:>5} KB")
        with open(os.path.join(HERE, "index.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"replays": index}, f, ensure_ascii=False, indent=2)
        return

    if not a.bot:
        ap.error("需要 bot 路径，或用 --all")
    trace = run(a.bot, a.fps)
    trace["name"] = os.path.basename(os.path.dirname(os.path.abspath(a.bot)))
    out = a.out or os.path.join(HERE, trace["name"] + ".json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(trace, f, separators=(",", ":"))
    print(f"{trace['name']}: {trace['score_m']} m -> {out}")


if __name__ == "__main__":
    main()
