"""把一次跳远导出成轻量回放 trace（JSON），供 replays/viewer.html 播放。

    python replays/trace.py Astra                 # 用资格赛里他最好那一跳
    python replays/trace.py Astra --final         # 用决赛里最好那一跳
    python replays/trace.py Astra --attempt 2     # 指定第几跳
    python replays/trace.py --all                 # 资格赛 16 人全导

风种子取成绩表里记录的值，所以导出的就是你看到的那个成绩。
"""
import argparse
import contextlib
import io
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(EP, "engine"))

import evaluator  # noqa: E402
import physics  # noqa: E402

PPM = physics.PPM


def spec():
    return {
        "torso": [physics.TORSO_W / PPM, physics.TORSO_H / PPM],
        "head_r": physics.HEAD_R / PPM,
        "thigh": [physics.THIGH_W / PPM, physics.THIGH_H / PPM],
        "shin": [physics.SHIN_W / PPM, physics.SHIN_H / PPM],
        "foot": [physics.FOOT_L / PPM, physics.FOOT_H / PPM],
        "arm": [physics.ARM_W / PPM, physics.ARM_H / PPM],
        "fore": [physics.FORE_W / PPM, physics.FORE_H / PPM],
    }


def _xyz(body):
    p = body.position
    return [round(p.x / PPM, 3), round(p.y / PPM, 3), round(body.angle, 3)]


def snapshot(runner, obs):
    head = runner.torso.local_to_world((0, physics.TORSO_H / 2 + physics.HEAD_R))
    row = [round(obs["time"], 3), round(obs["x"], 3)]   # 跳远没有逐帧成绩，记 x；成绩看 trace.score_m
    row += _xyz(runner.torso)
    row += [round(head.x / PPM, 3), round(head.y / PPM, 3)]
    for i in (0, 1):
        for group in (runner.thighs, runner.shins, runner.foot_bodies,
                      runner.arms, runner.forearms):
            row += _xyz(group[i])
    return row


def run(bot_path, attempt, fps=30):
    random.seed(20261105 + attempt["attempt"])
    act = evaluator.load_bot(bot_path)
    every = max(1, round(physics.FPS / fps))
    rows, n = [], 0

    def on_step(runner, obs):
        nonlocal n
        if n % every == 0:
            rows.append(snapshot(runner, obs))
        n += 1

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = evaluator.jump_attempt(act, attempt=attempt["attempt"],
                                     wind_seed=attempt["wind_seed"],
                                     wind_strength=attempt["wind_strength"],
                                     match_ctx=attempt["match_ctx"], on_step=on_step)
    rows.append(rows[-1])
    return {
        "fps": fps, "track": "jump", "score_m": round(res["score"], 3),
        "foul": res.get("foul"), "attempt": attempt["attempt"],
        "spec": spec(), "frames": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--attempt", type=int)
    a = ap.parse_args()

    physics.configure_jump("v4", "trial_old60_k0")
    board = json.load(open(os.path.join(EP, "results", "leaderboard.json"), encoding="utf-8"))
    stage = "final" if a.final else "qualification"
    rows = {r["name"]: r for r in board[stage]["results"]}
    subdir = "bots-final" if a.final else "bots"

    def pick(name):
        r = rows[name]
        if a.attempt:
            return next(x for x in r["attempts"] if x["attempt"] == a.attempt)
        return max(r["attempts"], key=lambda x: x["score"])

    names = list(rows) if a.all else [a.name]
    if not names or names == [None]:
        ap.error("给一个选手名，或用 --all")
    index = []
    for name in names:
        attempt = pick(name)
        trace = run(os.path.join(EP, subdir, name, "bot.py"), attempt)
        trace["name"] = name
        trace["stage"] = stage
        out = os.path.join(HERE, f"{name}{'-final' if a.final else ''}.json")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(trace, f, separators=(",", ":"), ensure_ascii=False)
        index.append({"name": name + ("（决赛）" if a.final else ""),
                      "file": os.path.basename(out), "score_m": trace["score_m"]})
        print(f'{name:<10} 第 {attempt["attempt"]} 跳  {trace["score_m"]:.3f} m  {os.path.getsize(out)//1024} KB')
    if a.all:
        with open(os.path.join(HERE, "index.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"replays": index}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

