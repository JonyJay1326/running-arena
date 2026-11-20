"""用你自己的 bot 挑战第 2 期跳远，三跳取最好。

    python challenge.py my_bot.py

规则和正式赛一样：同一个引擎、12 米起跳线、20 秒一跳、三跳取最好有效成绩。
风是随机抽的——这里用资格赛第 1 名（Astra）抽到的那三个风种子，所以名次只能参考。
"""
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

SEED_BASE = 20261105


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python challenge.py 你的bot.py")
    path = sys.argv[1]
    if not os.path.exists(path):
        sys.exit(f"找不到文件：{path}")

    physics.configure_jump("v4", "trial_old60_k0")
    with open(os.path.join(HERE, "results", "leaderboard.json"), encoding="utf-8") as f:
        board = json.load(f)

    winds = [a for a in board["qualification"]["results"][0]["attempts"]]
    best = 0.0
    scores = []
    print()
    for i, w in enumerate(winds, 1):
        random.seed(SEED_BASE + i)
        try:
            act = evaluator.load_bot(path)
        except Exception as exc:                       # noqa: BLE001
            sys.exit(f"bot 载入失败：{exc}")
        ctx = dict(round=i, best_so_far=round(best, 3), leader=0.0, rank=0,
                   qualification_line=0.0, bronze_line=0.0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = evaluator.jump_attempt(act, attempt=i, wind_seed=w["wind_seed"],
                                         wind_strength=w["wind_strength"], match_ctx=ctx)
        score = res["score"]
        scores.append(score)
        best = max(best, score)
        note = "" if res.get("foul") is None else f'犯规：{res["foul"]}'
        print(f'  第 {i} 跳：{score:6.3f} m  {note}')

    bests = [r["best"] for r in board["qualification"]["results"]]
    better = sum(1 for b in bests if b > best)
    rank = better + 1
    print(f"\n三跳最好：{best:.3f} m")
    print(f"放到资格赛 16 人里：第 {rank} 名（共 17 人）")
    if rank == 1:
        print("这一跳能赢正式赛的资格赛第一（5.187 m）。去 community/ 投稿吧。")
    elif rank <= 8:
        print("够进决赛（正式赛资格赛前 8）。")
    else:
        print("先保证每跳都有效，再谈距离——犯规和 0 分比跳得近更亏。")

    print("\n注意：风是逐跳随机抽的，正式赛每人抽到的风不一样；这个名次只做参考。")


if __name__ == "__main__":
    main()
