"""验证榜单：按成绩里记录的风种子，逐跳重放，和 results/leaderboard.json 比对。

    python verify.py                 # 资格赛 48 跳 + 决赛 24 跳
    python verify.py --stage qualification
    python verify.py --stage final
    python verify.py --limit 4       # 每个阶段只查前 4 名，快速冒烟

成绩里同时记着每一跳的 wind_seed 和 match_ctx，所以不需要重新抽风，逐跳重放即可。
"""
import argparse
import contextlib
import io
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))

try:
    import evaluator  # noqa: E402
    import physics  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "pymunk":
        sys.exit("缺少 pymunk。先运行：pip install -r ../requirements.txt（或 pip install pymunk）")
    raise

TOL = 1e-3
SEED_BASE = 20261105          # 与正式赛一致：random.seed(SEED_BASE + 第几跳)


def replay(path, attempt):
    random.seed(SEED_BASE + attempt["attempt"])
    act = evaluator.load_bot(path)
    with contextlib.redirect_stdout(io.StringIO()):
        res = evaluator.jump_attempt(
            act, attempt=attempt["attempt"], wind_seed=attempt["wind_seed"],
            wind_strength=attempt["wind_strength"], match_ctx=attempt["match_ctx"])
    return res


def check(stage_title, rows, subdir, limit=None):
    bad = 0
    rows = rows[:limit] if limit else rows
    print(f"\n== {stage_title} ==")
    print(f"{'#':>3}  {'选手':<16}{'榜单最好':>10}{'重放最好':>10}  结果")
    for r in rows:
        path = os.path.join(HERE, subdir, r["name"], "bot.py")
        scores = []
        stage_bad = 0
        for a in r["attempts"]:
            res = replay(path, a)
            got = round(res["score"], 4)
            scores.append(got)
            if abs(got - a["score"]) > TOL:
                stage_bad += 1
                print(f'    第 {a["attempt"]} 跳不一致：榜单 {a["score"]:.4f} / 重放 {got:.4f}')
        best = max(scores) if scores else 0.0
        ok = stage_bad == 0 and abs(best - r["best"]) <= TOL
        bad += 0 if ok else 1
        print(f'{r["rank"]:>3}  {r["name"]:<16}{r["best"]:>10.3f}{best:>10.3f}  {"OK" if ok else "MISMATCH"}')
    return bad


def check_extra(rows):
    bad = 0
    print("\n== 榜外补测（不计名次） ==")
    print(f"{'':>3}  {'选手':<16}{'榜单最好':>10}{'重放最好':>10}  结果")
    for r in rows:
        path = os.path.join(HERE, r["bot"])
        scores = []
        stage_bad = 0
        for a in r["attempts"]:
            got = round(replay(path, a)["score"], 4)
            scores.append(got)
            if abs(got - a["score"]) > TOL:
                stage_bad += 1
                print(f'    第 {a["attempt"]} 跳不一致：榜单 {a["score"]:.4f} / 重放 {got:.4f}')
        best = max(scores) if scores else 0.0
        ok = stage_bad == 0 and abs(best - r["best"]) <= TOL
        bad += 0 if ok else 1
        print(f'{"-":>3}  {r["name"]:<16}{r["best"]:>10.3f}{best:>10.3f}  {"OK" if ok else "MISMATCH"}')
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["qualification", "final", "all"], default="all")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    physics.configure_jump("v4", "trial_old60_k0")
    with open(os.path.join(HERE, "results", "leaderboard.json"), encoding="utf-8") as f:
        board = json.load(f)

    bad = 0
    if a.stage in ("qualification", "all"):
        bad += check("资格赛 16 人 × 3 跳", board["qualification"]["results"], "bots", a.limit)
    if a.stage in ("final", "all"):
        bad += check("决赛 8 人 × 3 跳", board["final"]["results"], "bots-final", a.limit)
        extra_path = os.path.join(HERE, "results", "extra.json")
        if os.path.exists(extra_path) and not a.limit:
            with open(extra_path, encoding="utf-8") as f:
                bad += check_extra(json.load(f)["results"])

    print("\n" + ("全部一致。" if bad == 0 else f"{bad} 人成绩对不上，检查引擎版本或 bot 文件。"))
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()

